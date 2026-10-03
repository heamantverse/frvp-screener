"""
levels_history.py  -  DAILY FRVP levels for index futures (NIFTY / BANKNIFTY / SENSEX)

For every date D it builds the FRVP of that day's own session (09:15-15:30,
1-minute candles of the nearest-expiry index future = what TradingView shows on
NIFTY1! at 1m) and writes: Day High / Low / Close, Point (PICK MOVE = POC),
VHIGH (1B = VAH), VLOW (ZB = VAL) and the fib levels built on ZB..1B.

TARGET_DATE examples:  2026-10-01
                       2026-09-30,2026-10-01
                       2026-09-01:2026-10-01      (every weekday in the range)

Secrets/env (same names as your other workflows):
  ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN (= ANGEL_PASSWORD secret), ANGEL_TOTP_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os, math, time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pyotp
import requests
from SmartApi import SmartConnect

TARGET_DATE = os.getenv("TARGET_DATE", "2026-10-01")
ROWS = 24                    # same as Pine "Rows"
VA_PCT = 0.70                # same as Pine "Value Area %"
TICK = 0.1                   # index future tick as shown on TradingView (1 decimal)

SCRIP_MASTER_URLS = [
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json",
]
INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]
FUT_EXCH = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}

FIB_RATIOS = [-1.618, -0.768, -0.618, -0.272, 0, 0.236, 0.618, 1, 1.236,
              1.618, 2, 2.414, 2.618, 3, 3.236]
FIB_NAMES = {0: "ZB", 1: "1B", 0.236: "REVERSAL", 0.618: "GOLDEN REVERSAL",
             1.236: "STALL", 1.618: "GR1", 2: "DD", -0.272: "DAYS LOW/HIGH",
             -0.618: "BOUNCER", -1.618: "IMPULSIVE BOUNCER"}


# ---------------------------------------------------------------- FRVP ----
def frvp(h, l, v, tick=TICK, rows=ROWS, va_pct=VA_PCT):
    h, l, v = map(lambda a: np.asarray(a, dtype=float), (h, l, v))
    r_high, r_low = h.max(), l.min()
    if r_high <= r_low:
        return None
    total_ticks = max(1, int(round((r_high - r_low) / tick)))
    raw = total_ticks / rows
    tpr_dn, tpr_up = max(1, math.floor(raw)), max(1, math.ceil(raw))
    rows_dn, rows_up = math.ceil(total_ticks / tpr_dn), math.ceil(total_ticks / tpr_up)
    tpr = tpr_dn if abs(rows_dn - rows) <= abs(rows_up - rows) else tpr_up

    lo, hi, px, left = [], [], r_low, total_ticks
    while left > 0:
        tt = min(tpr, left)
        lo.append(px)
        px += tt * tick
        hi.append(px)
        left -= tt
    lo, hi = np.array(lo), np.array(hi)
    n_rows = len(lo)

    ov = np.clip(np.minimum(h[:, None], hi[None, :]) - np.maximum(l[:, None], lo[None, :]), 0, None)
    br = (h - l)[:, None]
    frac = np.where(br > 0, ov / np.where(br > 0, br, 1), 0.0)
    for i in np.where((h - l) == 0)[0]:                  # zero-range candle -> row holding price
        frac[i, min(np.searchsorted(hi, h[i], side="left"), n_rows - 1)] = 1.0
    vol = (frac * v[:, None]).sum(axis=0)

    max_vol = vol.max()
    poc = int(np.argmax(vol))
    point = (lo[poc] + hi[poc]) / 2
    target = vol.sum() * va_pct
    accu, lo_i, hi_i = max_vol, poc, poc
    for _ in range(n_rows):
        if accu >= target:
            break
        up_v = vol[hi_i + 1] if hi_i < n_rows - 1 else -1.0
        dn_v = vol[lo_i - 1] if lo_i > 0 else -1.0
        if up_v < 0 and dn_v < 0:
            break
        if up_v < 0:
            up = False
        elif dn_v < 0:
            up = True
        elif up_v != dn_v:
            up = up_v > dn_v
        else:
            up = (hi_i + 1 - poc) <= (poc - (lo_i - 1))
        nxt = up_v if up else dn_v
        if accu + nxt > target:
            break
        if up:
            hi_i += 1
        else:
            lo_i -= 1
        accu += nxt
    return point, hi[hi_i], lo[lo_i]          # Point (POC), VHIGH (1B), VLOW (ZB)


# ----------------------------------------------------------- Angel One ----
def login():
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    res = api.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_PIN"], totp)
    if not res or not res.get("status"):
        raise SystemExit(f"Angel login failed: {res}")
    return api


def candles(api, exch, token, interval, start, end, tries=3):
    params = {"exchange": exch, "symboltoken": str(token), "interval": interval,
              "fromdate": start.strftime("%Y-%m-%d %H:%M"),
              "todate": end.strftime("%Y-%m-%d %H:%M")}
    for t in range(tries):
        try:
            res = api.getCandleData(params)
            if res and res.get("status") and res.get("data"):
                return pd.DataFrame(res["data"], columns=["t", "o", "h", "l", "c", "v"])
            print("   no data:", res.get("message") if isinstance(res, dict) else res)
        except Exception as e:
            print("   retry", t + 1, e)
        time.sleep(1.5 * (t + 1))
    return None


def load_master():
    last = None
    for url in SCRIP_MASTER_URLS:
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            df = pd.DataFrame(r.json())
            print("scrip master loaded:", len(df), "rows from", url)
            return df
        except Exception as e:
            print("scrip master failed:", url, e)
            last = e
    raise SystemExit(f"Could not load Angel scrip master: {last}")


def fut_contract(master, name, d):
    """Nearest-expiry index future with expiry on/after date d (what NIFTY1! shows)."""
    exch = FUT_EXCH[name]
    m = master[(master["instrumenttype"] == "FUTIDX") & (master["name"] == name)
               & (master["exch_seg"] == exch)].copy()
    if m.empty:
        return None
    m["exp"] = pd.to_datetime(m["expiry"], format="%d%b%Y", errors="coerce")
    m = m[m["exp"] >= pd.Timestamp(d.date())].sort_values("exp")
    if m.empty:
        return None
    row = m.iloc[0]
    return exch, str(row["token"]), str(row["symbol"])


# ---------------------------------------------------------------- misc ----
def parse_dates(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            a, b = [datetime.strptime(x.strip(), "%Y-%m-%d") for x in part.split(":")]
            while a <= b:
                if a.weekday() < 5:
                    out.append(a)
                a += timedelta(days=1)
        else:
            out.append(datetime.strptime(part, "%Y-%m-%d"))
    return out


def fib_label(r):
    nm = FIB_NAMES.get(r, "")
    return f"{r:g} {nm}".strip()


def make_row(d, sym, contract, bars, df, r):
    point, vhigh, vlow = r
    rng = vhigh - vlow
    row = {"Date": d.strftime("%Y-%m-%d"), "Symbol": sym, "Contract": contract, "Bars": bars,
           "Day High": df["h"].max(), "Day Low": df["l"].min(), "Day Close": df["c"].iloc[-1],
           "Point (PICK MOVE)": point, "VHIGH (1B)": vhigh, "VLOW (ZB)": vlow}
    for ratio in FIB_RATIOS:
        row[fib_label(ratio)] = vlow + rng * ratio
    return row


# ---------------------------------------------------------------- main ----
def main():
    dates = parse_dates(TARGET_DATE)
    api = login()
    master = load_master()
    out = []
    for d in dates:
        s, e = d.replace(hour=9, minute=15), d.replace(hour=15, minute=30)
        for sym in INDICES:
            c = fut_contract(master, sym, d)
            if c is None:
                print(f"{d:%Y-%m-%d} {sym}: no active futures contract found")
                continue
            ex, tok, fs = c
            print(f"-> {d:%Y-%m-%d} {sym} {fs}")
            df = candles(api, ex, tok, "ONE_MINUTE", s, e)
            time.sleep(0.5)
            if df is None or len(df) < 50:
                print("   skipped (no/low data)")
                continue
            r = frvp(df["h"], df["l"], df["v"])
            if r is not None:
                out.append(make_row(d, sym, fs, len(df), df, r))

    if not out:
        raise SystemExit("No data produced")
    df = pd.DataFrame(out).round(2)
    fn = f"levels_{dates[0]:%Y-%m-%d}_{dates[-1]:%Y-%m-%d}.xlsx" if len(dates) > 1 else f"levels_{dates[0]:%Y-%m-%d}.xlsx"
    with pd.ExcelWriter(fn, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Levels")
        ws = xw.sheets["Levels"]
        ws.freeze_panes = "D2"
        for col in ws.columns:
            w = max(len(str(c.value)) for c in col if c.value is not None)
            ws.column_dimensions[col[0].column_letter].width = min(max(11, w + 2), 40)
    print(df.T.to_string())

    tok_, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok_ and chat:
        with open(fn, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{tok_}/sendDocument",
                          data={"chat_id": chat, "caption": f"FRVP levels {TARGET_DATE}"},
                          files={"document": f}, timeout=60)


if __name__ == "__main__":
    main()
