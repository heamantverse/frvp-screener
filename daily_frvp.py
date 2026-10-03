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
import os, io, base64, time
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
COLS = ["Date", "Symbol", "Contract", "Bars", "Day High", "Day Low", "Day Close",
        "Point", "VHIGH", "VLOW", "Total Volume"]


# --------------------------------------------------------- GitHub data repo
def _gh_headers():
    return {"Authorization": f"Bearer {DATA_TOKEN}", "Accept": "application/vnd.github+json"}


def gh_read():
    url = f"https://api.github.com/repos/{DATA_REPO}/contents/{HIST_PATH}"
    r = requests.get(url, headers=_gh_headers(), timeout=60)
    if r.status_code == 404:
        return pd.DataFrame(columns=COLS), None
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
def tg_message(text):
    if TG_TOKEN and TG_CHAT:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": text}, timeout=60)


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


def next_weekday(d):
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def is_last_weekday_of_month(d):
    return next_weekday(d).month != d.month


def next_day_message(rows):
    if not rows:
        return None
    d = datetime.strptime(rows[0]["Date"], "%Y-%m-%d")
    nd = next_weekday(d)
    lines = [f"Levels for {nd:%d %b %Y} (from FRVP of {d:%d %b %Y})"]
    for r in rows:
        lines.append(f"\n{r['Symbol']}  ({r['Contract']})")
        for k, v in fib_cols(r["VLOW"], r["VHIGH"]).items():
            lines.append(f"{k}: {v}")
        lines.append(f"PICK MOVE (Point): {r['Point']}")
    return "\n".join(lines)


# ---------------------------------------------------------- month file ----
def build_month_file(hist, ym, path):
    h = hist.copy()
    h["Date"] = h["Date"].astype(str)
    lv = []
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
            lv.append(row)
    lev = pd.DataFrame(lv).sort_values(["Date", "Symbol"]) if lv else pd.DataFrame()
    raw = h[h["Date"].str.startswith(ym)].sort_values(["Date", "Symbol"])
    if lev.empty and raw.empty:
        return False
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        lev.to_excel(xw, index=False, sheet_name="Levels")
        raw.to_excel(xw, index=False, sheet_name="FRVP")
        for name in ("Levels", "FRVP"):
            ws = xw.sheets[name]
            ws.freeze_panes = "C2"
            for col in ws.columns:
                w = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max(11, w + 2), 34)
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
        dates = LH.parse_dates(DATES) if DATES else ([today] if today.weekday() < 5 else [])
        if not dates:
            print("weekend - nothing to do")
        else:
            api = LH.login()
            master = LH.load_master()
            for d in dates:
                new += compute_day(api, master, d)
            if new:
                hist = upsert(hist, new)
                gh_write(hist, sha, f"FRVP {dates[0]:%Y-%m-%d}..{dates[-1]:%Y-%m-%d}")
                last = max(r["Date"] for r in new)
                msg = next_day_message([r for r in new if r["Date"] == last])
                if msg:
                    tg_message(msg)
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
