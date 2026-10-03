"""
daily_frvp.py  -  runs after market close (Mon-Fri)

1) Builds today's FRVP (1-min bars of the nearest-expiry index future) for
   NIFTY / BANKNIFTY / SENSEX and saves it in the private data repo
   (frvp_daily.csv  ->  one row per Date+Symbol).
2) Sends tomorrow's fib levels (built from today's FRVP) on Telegram.
3) On the last weekday of the month (or when SEND_MONTH=YYYY-MM is given) it
   sends the whole-month Excel:  "Levels" sheet = for each date, the fib levels
   that come from the PREVIOUS trading day's FRVP (30 Sep FRVP -> 1 Oct levels)
   next to that day's actual High/Low/Close, and an "FRVP" sheet with raw data.

Env:
  ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, ANGEL_TOTP_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  DATA_REPO   (default heamantverse/scope-data)    DATA_TOKEN (token with contents write)
  DATES       optional, e.g. 2026-09-30,2026-10-01  (empty = today, IST)
  SEND_MONTH  optional, e.g. 2026-10  (send that month's file now)
"""
import os, io, base64, time, html
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from openpyxl import Workbook
import requests

import levels_history as LH

DATA_REPO = os.getenv("DATA_REPO", "heamantverse/scope-data")
DATA_TOKEN = os.getenv("DATA_TOKEN", "")
HIST_PATH = os.getenv("HIST_PATH", "frvp_daily.csv")
DATES = os.getenv("DATES", "").strip()
SEND_MONTH = os.getenv("SEND_MONTH", "").strip()
TG_TOKEN, TG_CHAT = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
IST = timezone(timedelta(hours=5, minutes=30))
# market holidays (YYYY-MM-DD, comma separated) so "next trading day" skips them; add more via env HOLIDAYS
HOLIDAYS = {x.strip() for x in os.getenv("HOLIDAYS", "2026-10-02").split(",") if x.strip()}
COLS = ["Date", "Symbol", "Contract", "Bars", "Day High", "Day Low", "Day Close",
        "Point", "VHIGH", "VLOW", "Total Volume"]


# --------------------------------------------------------- GitHub data repo
def _gh_headers():
    return {"Authorization": f"Bearer {DATA_TOKEN}", "Accept": "application/vnd.github+json"}


def gh_read():
    url = f"https://api.github.com/repos/{DATA_REPO}/contents/{HIST_PATH}"
    r = requests.get(url, headers=_gh_headers(), timeout=60)
    if r.status_code == 401:
        raise SystemExit("GitHub 401: DATA_TOKEN is invalid/expired. Create a new fine-grained token "
                         "(repo scope-data, Contents: Read and write) and update the secret.")
    if r.status_code == 404:
        chk = requests.get(f"https://api.github.com/repos/{DATA_REPO}", headers=_gh_headers(), timeout=60)
        if chk.status_code != 200:
            raise SystemExit(f"GitHub {chk.status_code}: token cannot see repo {DATA_REPO} "
                             "(wrong repo name or token has no access to it).")
        return pd.DataFrame(columns=COLS), None      # repo ok, file not created yet
    r.raise_for_status()
    j = r.json()
    text = base64.b64decode(j["content"]).decode()
    return pd.read_csv(io.StringIO(text)), j["sha"]


def gh_write(df, sha, msg):
    url = f"https://api.github.com/repos/{DATA_REPO}/contents/{HIST_PATH}"
    body = {"message": msg, "content": base64.b64encode(df.to_csv(index=False).encode()).decode()}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=body, timeout=60)
    r.raise_for_status()


def upsert(hist, new):
    df = pd.concat([hist, pd.DataFrame(new, columns=COLS)], ignore_index=True)
    df["Date"] = df["Date"].astype(str)
    df = df.drop_duplicates(["Date", "Symbol"], keep="last")
    return df.sort_values(["Symbol", "Date"]).reset_index(drop=True)


# ------------------------------------------------------------- telegram ----
def tg_message(text, html_mode=False):
    if TG_TOKEN and TG_CHAT:
        data = {"chat_id": TG_CHAT, "text": text}
        if html_mode:
            data["parse_mode"] = "HTML"
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=60)


def tg_doc(path, caption):
    if TG_TOKEN and TG_CHAT:
        with open(path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument",
                          data={"chat_id": TG_CHAT, "caption": caption},
                          files={"document": f}, timeout=90)


# ------------------------------------------------------------- compute ----
def compute_day(api, master, d):
    out = []
    s, e = d.replace(hour=9, minute=15), d.replace(hour=15, minute=30)
    for sym in LH.INDICES:
        c = LH.fut_contract(master, sym, d)
        if c is None:
            print(f"{d:%Y-%m-%d} {sym}: no active futures contract")
            continue
        ex, tok, fs = c
        df = LH.candles(api, ex, tok, "ONE_MINUTE", s, e)
        time.sleep(0.5)
        if df is None or len(df) < 50:
            print(f"{d:%Y-%m-%d} {sym}: skipped (no/low data - holiday?)")
            continue
        r = LH.frvp(df["h"], df["l"], df["v"])
        if r is None:
            continue
        point, vhigh, vlow = r
        out.append({"Date": d.strftime("%Y-%m-%d"), "Symbol": sym, "Contract": fs,
                    "Bars": len(df), "Day High": float(df["h"].max()), "Day Low": float(df["l"].min()),
                    "Day Close": float(df["c"].iloc[-1]), "Point": round(float(point), 2),
                    "VHIGH": round(float(vhigh), 2), "VLOW": round(float(vlow), 2),
                    "Total Volume": float(df["v"].sum())})
        print(f"OK {d:%Y-%m-%d} {sym} {fs}: ZB {vlow:.2f}  Point {point:.2f}  1B {vhigh:.2f}")
    return out


def fib_cols(vlow, vhigh):
    rng = vhigh - vlow
    return {LH.fib_label(r): round(vlow + rng * r, 2) for r in LH.FIB_RATIOS}


def level_rows(vlow, vhigh, point):
    """Long format: one row per level (fibs on ZB..1B + PICK MOVE)."""
    rng = vhigh - vlow
    out = [{"Level": r, "Name": LH.FIB_NAMES.get(r, ""), "Price": round(vlow + rng * r, 2)}
           for r in LH.FIB_RATIOS]
    out.append({"Level": None, "Name": "PICK MOVE", "Price": round(float(point), 2)})
    return out


def next_weekday(d):
    n = d + timedelta(days=1)
    while n.weekday() >= 5 or n.strftime("%Y-%m-%d") in HOLIDAYS:
        n += timedelta(days=1)
    return n


def is_last_weekday_of_month(d):
    return next_weekday(d).month != d.month


def html_table(rows):
    rows = sorted(rows, key=lambda x: -x["Price"])           # highest price on top, like the chart
    lines = [f"{'Level':<8}{'Name':<19}{'Price':>9}"]
    for x in rows:
        lvl = "POC" if x["Level"] is None else f"{x['Level']:g}"
        lines.append(f"{lvl:<8}{x['Name']:<19}{x['Price']:>9.2f}")
    return html.escape("\n".join(lines))


def next_day_pack(rows):
    """Telegram HTML text + long-format DataFrame for tomorrow's levels."""
    d = datetime.strptime(rows[0]["Date"], "%Y-%m-%d")
    nd = next_weekday(d)
    parts = [f"<b>Levels for {nd:%d %b %Y}</b> (from FRVP of {d:%d %b %Y})"]
    long = []
    for r in rows:
        lv = level_rows(r["VLOW"], r["VHIGH"], r["Point"])
        parts.append(f"\n<b>{r['Symbol']}</b>  {r['Contract']}\n<pre>{html_table(lv)}</pre>")
        for x in lv:
            long.append({"Date": nd.strftime("%Y-%m-%d"), "Symbol": r["Symbol"], "Contract": r["Contract"],
                         "Based on FRVP of": r["Date"], **x})
    return "\n".join(parts), pd.DataFrame(long), nd


def style_sheet(ws):
    """Simple header + filter for plain sheets."""
    from openpyxl.styles import Font, PatternFill
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDDDDD")
    for col in ws.columns:
        w = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(11, w + 2), 34)


def write_index_sheet(wb, sym, rows, with_actual):
    """One sheet per index. One row per date: FRVP of the previous day + fib levels (+ actual H/L/C)."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    grey, head, touch = (PatternFill("solid", fgColor=c) for c in ("D0D0D0", "EDEDED", "FFF2CC"))
    ratios = LH.FIB_RATIOS
    heads = ["Date", "Based on FRVP of", "Contract", "VLOW (ZB)", "Point (PICK MOVE)", "VHIGH (1B)"]
    heads += [f"{r:g}\n{LH.FIB_NAMES.get(r, '')}".strip() for r in ratios]
    if with_actual:
        heads += ["Day High", "Day Low", "Day Close"]
    n, f0, f1 = len(heads), 7, 6 + len(ratios)

    ws = wb.create_sheet(sym)
    ws.append([""] * n)                                   # row 1: group band
    groups = [((1, 3), ""), ((4, 6), "FRVP (previous day)"), ((f0, f1), "FIB LEVELS")]
    if with_actual:
        groups.append(((f1 + 1, n), "ACTUAL (that day)"))
    for (c0, c1), txt in groups:
        ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c1)
        ws.cell(1, c0, txt)
    for c in range(1, n + 1):
        cell = ws.cell(1, c)
        cell.fill, cell.font = grey, Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    ws.append(heads)                                      # row 2: headers (filter row)
    for c in range(1, n + 1):
        cell = ws.cell(2, c)
        cell.fill, cell.font = head, Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 34

    for r in rows:
        fibs = [round(r["vlow"] + (r["vhigh"] - r["vlow"]) * x, 2) for x in ratios]
        vals = [r["date"], r["based"], r["contract"], r["vlow"], r["point"], r["vhigh"]] + fibs
        if with_actual:
            vals += [r["hi"], r["lo"], r["cl"]]
        ws.append(vals)
        i = ws.max_row
        for c in range(4, n + 1):
            ws.cell(i, c).number_format = "0.00"
        if with_actual:                                   # yellow = price touched this level that day
            for c in range(4, f1 + 1):
                v = ws.cell(i, c).value
                if r["lo"] <= v <= r["hi"]:
                    ws.cell(i, c).fill = touch
    ws.freeze_panes = "D3"
    ws.auto_filter.ref = f"A2:{get_column_letter(n)}{max(ws.max_row, 2)}"
    for c in range(1, n + 1):
        ws.column_dimensions[get_column_letter(c)].width = 13 if c > 3 else (12 if c == 1 else 17)
    ws.column_dimensions["E"].width = 15


def build_next_day_file(rows, nd, path):
    wb = Workbook()
    wb.remove(wb.active)
    for sym in LH.INDICES:
        rs = [{"date": nd.strftime("%Y-%m-%d"), "based": r["Date"], "contract": r["Contract"],
               "vlow": r["VLOW"], "point": r["Point"], "vhigh": r["VHIGH"]} for r in rows if r["Symbol"] == sym]
        if rs:
            write_index_sheet(wb, sym, rs, with_actual=False)
    wb.save(path)


# ---------------------------------------------------------- month file ----
def build_month_file(hist, ym, path):
    h = hist.copy()
    h["Date"] = h["Date"].astype(str)
    wb = Workbook()
    wb.remove(wb.active)
    any_rows = False
    for sym in LH.INDICES:
        g = h[h["Symbol"] == sym].sort_values("Date").reset_index(drop=True)
        rs = []
        for i in range(1, len(g)):
            cur, prev = g.iloc[i], g.iloc[i - 1]
            if str(cur["Date"]).startswith(ym):
                rs.append({"date": cur["Date"], "based": prev["Date"], "contract": prev["Contract"],
                           "vlow": float(prev["VLOW"]), "point": float(prev["Point"]),
                           "vhigh": float(prev["VHIGH"]), "hi": float(cur["Day High"]),
                           "lo": float(cur["Day Low"]), "cl": float(cur["Day Close"])})
        if rs:
            any_rows = True
            write_index_sheet(wb, sym, rs, with_actual=True)
    raw = h[h["Date"].str.startswith(ym)].sort_values(["Symbol", "Date"])
    if raw.empty and not any_rows:
        return False
    ws = wb.create_sheet("FRVP (raw)")
    ws.append(list(raw.columns))
    for row in raw.itertuples(index=False):
        ws.append(list(row))
    style_sheet(ws)
    wb.save(path)
    return True


# ---------------------------------------------------------------- main ----
def main():
    if not DATA_TOKEN:
        raise SystemExit("DATA_TOKEN missing (token for the private data repo)")
    today = datetime.now(IST).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    hist, sha = gh_read()
    months = set()
    new = []

    if DATES or not SEND_MONTH:
        dates = LH.parse_dates(DATES) if DATES else ([today] if today.weekday() < 5 and today.strftime("%Y-%m-%d") not in HOLIDAYS else [])
        if not dates:
            print("weekend/holiday - nothing to do")
        else:
            api = LH.login()
            master = LH.load_master()
            for d in dates:
                new += compute_day(api, master, d)
            if new:
                hist = upsert(hist, new)
                gh_write(hist, sha, f"FRVP {dates[0]:%Y-%m-%d}..{dates[-1]:%Y-%m-%d}")
                last = max(r["Date"] for r in new)
                last_rows = [r for r in new if r["Date"] == last]
                msg, long_df, nd = next_day_pack(last_rows)
                tg_message(msg, html_mode=True)
                nf = f"levels_for_{nd:%Y-%m-%d}.xlsx"
                build_next_day_file(last_rows, nd, nf)
                tg_doc(nf, f"Levels for {nd:%d %b %Y} (filterable)")
                if not DATES and is_last_weekday_of_month(today):
                    months.add(today.strftime("%Y-%m"))
            else:
                print("no data produced (market holiday?)")

    if SEND_MONTH:
        months.add(SEND_MONTH)

    for ym in sorted(months):
        path = f"frvp_levels_{ym}.xlsx"
        if build_month_file(hist, ym, path):
            tg_doc(path, f"FRVP levels - {ym} (each day's fibs come from the previous day's FRVP)")
            print("sent month file", path)
        else:
            print("no rows for month", ym)


if __name__ == "__main__":
    main()
