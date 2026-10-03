"""
levels_history.py  -  FRVP levels for ONE date (default 2026-10-01)

For the target date D it uses ONLY candles BEFORE D (4-month lookback of
30-min candles) to calculate Point (POC), VHIGH (VAH), VLOW (VAL) exactly the
way the SCOPE Pine Script does, then adds fib levels on VLOW..VHIGH and the
Day High / Day Low of D.  Output: Excel (+ sent to Telegram).

Env vars (use the SAME secret names as your screener_job.py workflow):
  ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, ANGEL_TOTP_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  TARGET_DATE (optional, YYYY-MM-DD)
"""
import os, math, time, json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pyotp
import requests
from SmartApi import SmartConnect

TARGET_DATE = os.getenv("TARGET_DATE", "2026-10-01")
LOOKBACK_DAYS = 122          # ~4 months
ROWS = 24                    # same as Pine "Rows"
VA_PCT = 0.70                # same as Pine "Value Area %"
TICK = 0.05                  # NSE equity tick
FIBS = [("Fib 0 (Break down)", 0.0), ("Fib 0.25 (Buy Reversal)", 0.25),
        ("Fib 0.75 (Sell Reversal)", 0.75), ("Fib 1 (Breakout)", 1.0),
        ("Fib 1.272 (Target 1)", 1.272)]

SCRIP_MASTER = "https://margincalculator.angelbroking.com/OpenAPI_Files/OpenAPIScripMaster.json"
FUT_EXCH = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}

# Indices only: name -> (exchange, Angel index token)
INDICES = {"NIFTY": ("NSE", "99926000"),
           "BANKNIFTY": ("NSE", "99926009"),
           "SENSEX": ("BSE", "99919000")}


# ---------------------------------------------------------------- FRVP ----
def frvp(h, l, v, tick=TICK, rows=ROWS, va_pct=VA_PCT):
    h, l, v = map(np.asarray, (h, l, v))
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

    ov = np.minimum(h[:, None], hi[None, :]) - np.maximum(l[:, None], lo[None, :])
    ov = np.clip(ov, 0, None)
    br = (h - l)[:, None]
    frac = np.where(br > 0, ov / np.where(br > 0, br, 1), 0.0)
    flat = (h - l) == 0                       # zero-range candles -> row holding the price
    if flat.any():
        for i in np.where(flat)[0]:
            idx = np.searchsorted(hi, h[i], side="left")
            frac[i, min(idx, n_rows - 1)] = 1.0
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
    return point, hi[hi_i], lo[lo_i]          # Point, VHIGH, VLOW


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
                df = pd.DataFrame(res["data"], columns=["t", "o", "h", "l", "c", "v"])
                return df
        except Exception as e:
            print("   retry", t + 1, e)
        time.sleep(1.5 * (t + 1))
    return None


# ------------------------------------------------------------ futures ----
def load_master():
    r = requests.get(SCRIP_MASTER, timeout=120)
    r.raise_for_status()
    return pd.DataFrame(r.json())


def fut_contract(master, name, target):
    """Nearest-expiry index future whose expiry is on/after the target date."""
    exch = FUT_EXCH[name]
    m = master[(master["instrumenttype"] == "FUTIDX") & (master["name"] == name)
               & (master["exch_seg"] == exch)].copy()
    if m.empty:
        return None
    m["exp"] = pd.to_datetime(m["expiry"], format="%d%b%Y", errors="coerce")
    m = m[m["exp"] >= pd.Timestamp(target.date())].sort_values("exp")
    if m.empty:
        return None
    row = m.iloc[0]
    return exch, str(row["token"]), str(row["symbol"])


def make_row(sym, source, day, r):
    point, vhigh, vlow = r
    rng = vhigh - vlow
    row = {"Date": TARGET_DATE, "Symbol": sym, "Source": source,
           "Day High": day["h"].iloc[0], "Day Low": day["l"].iloc[0],
           "Day Close": day["c"].iloc[0],
           "Point": point, "VHIGH": vhigh, "VLOW": vlow}
    for name, ratio in FIBS:
        row[name] = vlow + rng * ratio
    return row


# ---------------------------------------------------------------- main ----
def main():
    d = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
    lb_start = (d - timedelta(days=LOOKBACK_DAYS)).replace(hour=9, minute=15)
    lb_end = (d - timedelta(days=1)).replace(hour=15, minute=30)   # nothing from target day
    day_start, day_end = d.replace(hour=9, minute=15), d.replace(hour=15, minute=30)

    api = login()
    try:
        master = load_master()
    except Exception as e:
        print("scrip master failed:", e)
        master = None

    out = []
    for sym, (exch, tok) in INDICES.items():
        # ---------- A) SPOT index (volume if present, else equal weight)
        print("->", sym, "SPOT")
        intr = candles(api, exch, tok, "THIRTY_MINUTE", lb_start, lb_end)
        time.sleep(0.4)
        day = candles(api, exch, tok, "ONE_DAY", day_start, day_end)
        time.sleep(0.4)
        if intr is None or day is None or len(intr) < 20:
            print("   spot skipped (no data)")
        else:
            vols = intr["v"].astype(float)
            basis = "SPOT - index volume"
            if vols.sum() <= 0:
                vols = np.ones(len(intr))
                basis = "SPOT - equal-weight (no volume)"
            r = frvp(intr["h"], intr["l"], vols)
            if r is not None:
                out.append(make_row(sym, basis, day, r))

        # ---------- B) FUTURES (price + volume of nearest-expiry contract)
        if master is None:
            continue
        c = fut_contract(master, sym, d)
        if c is None:
            print("   no futures contract found for", sym)
            continue
        fx, ft, fs = c
        print("->", sym, "FUT", fs, ft)
        fi = candles(api, fx, ft, "THIRTY_MINUTE", lb_start, lb_end)
        time.sleep(0.4)
        fd = candles(api, fx, ft, "ONE_DAY", day_start, day_end)
        time.sleep(0.4)
        if fi is None or fd is None or len(fi) < 20:
            print("   futures skipped (no data)")
            continue
        first = str(fi["t"].iloc[0])[:10]
        days = fi["t"].astype(str).str[:10].nunique()
        r = frvp(fi["h"], fi["l"], fi["v"].astype(float))
        if r is not None:
            out.append(make_row(sym, f"FUT {fs} (data from {first}, {days} days)", fd, r))

    if not out:
        raise SystemExit("No data produced")
    df = pd.DataFrame(out).round(2)
    fn = f"levels_{TARGET_DATE}.xlsx"
    with pd.ExcelWriter(fn, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Levels")
        ws = xw.sheets["Levels"]
        ws.freeze_panes = "D2"
        for col in ws.columns:
            w = max(len(str(c.value)) for c in col if c.value is not None)
            ws.column_dimensions[col[0].column_letter].width = min(max(12, w + 2), 48)
    print(df.to_string())

    tok_, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok_ and chat:
        with open(fn, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{tok_}/sendDocument",
                          data={"chat_id": chat, "caption": f"Levels for {TARGET_DATE}"},
                          files={"document": f}, timeout=60)


if __name__ == "__main__":
    main()
