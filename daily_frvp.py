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
    from openpyxl.styles import Font, PatternFill
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions                      # filter arrows on every column
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDDDDD")
    for col in ws.columns:
        w = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(11, w + 2), 34)


# ---------------------------------------------------------- month file ----
def build_month_file(hist, ym, path):
    h = hist.copy()
    h["Date"] = h["Date"].astype(str)
    wide, long = [], []
    for sym, g in h.sort_values("Date").groupby("Symbol"):
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            cur, prev = g.iloc[i], g.iloc[i - 1]
            if not str(cur["Date"]).startswith(ym):
                continue
            row = {"Date": cur["Date"], "Symbol": sym, "Based on FRVP of": prev["Date"],
                   "Prev Point": prev["Point"], "Prev VHIGH (1B)": prev["VHIGH"],
                   "Prev VLOW (ZB)": prev["VLOW"]}
            row.update(fib_cols(prev["VLOW"], prev["VHIGH"]))
            row.update({"Day High": cur["Day High"], "Day Low": cur["Day Low"],
                        "Day Close": cur["Day Close"]})
            wide.append(row)
            for x in level_rows(prev["VLOW"], prev["VHIGH"], prev["Point"]):
                long.append({"Date": cur["Date"], "Symbol": sym, "Based on FRVP of": prev["Date"], **x,
                             "Day High": cur["Day High"], "Day Low": cur["Day Low"],
                             "Day Close": cur["Day Close"],
                             "Touched": "Y" if cur["Day Low"] <= x["Price"] <= cur["Day High"] else "N"})
    wide = pd.DataFrame(wide).sort_values(["Date", "Symbol"]) if wide else pd.DataFrame()
    long = pd.DataFrame(long).sort_values(["Date", "Symbol", "Price"], ascending=[True, True, False]) if long else pd.DataFrame()
    raw = h[h["Date"].str.startswith(ym)].sort_values(["Date", "Symbol"])
    if wide.empty and raw.empty:
        return False
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        long.to_excel(xw, index=False, sheet_name="Level list")
        wide.to_excel(xw, index=False, sheet_name="Levels (wide)")
        raw.to_excel(xw, index=False, sheet_name="FRVP")
        for name in ("Level list", "Levels (wide)", "FRVP"):
            style_sheet(xw.sheets[name])
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
                with pd.ExcelWriter(nf, engine="openpyxl") as xw:
                    long_df.to_excel(xw, index=False, sheet_name="Levels")
                    style_sheet(xw.sheets["Levels"])
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
