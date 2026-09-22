"""
Pre-market Nifty & BankNifty alert.
Gai kali ni High-Low range parthi index na fib levels ganine bias (CALL/PUT)
nakki kare chhe, pachi e disha na ATM option (chalu week expiry) na potana
data parthi POC/VAH/VAL + SL/Target ganine PDF banave chhe.
"""

import os
import time
import json
import numpy as np
import pandas as pd
import requests
import pyotp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from SmartApi import SmartConnect

API_KEY = os.environ["ANGEL_API_KEY"]
CLIENT_CODE = os.environ["ANGEL_CLIENT_ID"]
PASSWORD = os.environ["ANGEL_PASSWORD"]
TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"]

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

INDEXES = [
    {"name": "NIFTY", "token": "99926000", "step": 50},
    {"name": "BANKNIFTY", "token": "99926009", "step": 100},
]

LEVELS = [
    (0.0,    "Break down",       "PUT"),
    (0.25,   "Buy Reversal",     "CALL"),
    (0.75,   "Sell Reversal",    "PUT"),
    (1.0,    "Break out",        "CALL"),
    (1.272,  "Target 1",         "WATCH"),
    (-0.272, "Target 1 (down)",  "WATCH"),
]

FIB_RATIOS = [1.618, 1.272, 1.0, 0.786, 0.618, 0.5, 0.382, 0.236, 0.0, -0.272, -1.618]

OUT_FILE = "index_alert.json"


def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    return smart_api


def fib(ratio, lo, hi):
    return lo + (hi - lo) * ratio


def prev_day_range(smart_api, token):
    end = datetime.now()
    start = end - timedelta(days=10)
    resp = smart_api.getCandleData({
        "exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
        "fromdate": start.strftime("%Y-%m-%d 09:15"),
        "todate": end.strftime("%Y-%m-%d 15:30"),
    })
    rows = resp.get("data") or []
    if not rows:
        return None
    last = rows[-1]
    return {"date": last[0][:10], "high": float(last[2]), "low": float(last[3]), "close": float(last[4])}


def get_ltp(smart_api, token, name, exchange="NSE"):
    try:
        resp = smart_api.ltpData(exchange, name, token)
        if resp.get("status") and resp.get("data"):
            return float(resp["data"]["ltp"])
    except Exception as e:
        print(f"[WARN] LTP failed for {name}: {e}")
    return None


def nearest_level(price, lo, hi):
    computed = sorted(
        [(ratio, label, action, fib(ratio, lo, hi)) for ratio, label, action in LEVELS],
        key=lambda x: x[0],
    )
    best_idx = min(range(len(computed)), key=lambda i: abs(price - computed[i][3]))
    ratio, label, action, lvl_price = computed[best_idx]
    below = computed[best_idx - 1] if best_idx > 0 else None
    above = computed[best_idx + 1] if best_idx < len(computed) - 1 else None

    if action == "CALL":
        sl, tp = (below[3] if below else lvl_price), (above[3] if above else lvl_price)
    elif action == "PUT":
        sl, tp = (above[3] if above else lvl_price), (below[3] if below else lvl_price)
    else:
        sl = tp = None

    return {
        "ratio": ratio, "label": label, "action": action, "level_price": round(lvl_price, 2),
        "sl": round(sl, 2) if sl is not None else None,
        "tp": round(tp, 2) if tp is not None else None,
    }


# ============================================================
# OPTION CHAIN — nearest weekly expiry, ATM strike
# ============================================================

def load_master():
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    return df


def find_atm_option(master_df, name, ltp, step, opt_type):
    """opt_type: 'CE' or 'PE'. Returns dict with token, tradingsymbol, strike, expiry — or None."""
    opts = master_df[
        (master_df["name"] == name)
        & (master_df["exch_seg"] == "NFO")
        & (master_df["instrumenttype"] == "OPTIDX")
        & (master_df["symbol"].str.endswith(opt_type))
    ].copy()
    if opts.empty:
        return None

    opts["expiry_dt"] = pd.to_datetime(opts["expiry"], errors="coerce")
    opts = opts.dropna(subset=["expiry_dt"])
    if opts.empty:
        return None

    today = pd.Timestamp.now().normalize()
    upcoming = opts[opts["expiry_dt"] >= today]
    if upcoming.empty:
        return None
    nearest_expiry = upcoming["expiry_dt"].min()
    opts = upcoming[upcoming["expiry_dt"] == nearest_expiry].copy()

    opts["strike_val"] = pd.to_numeric(opts["strike"], errors="coerce") / 100.0
    opts = opts.dropna(subset=["strike_val"])
    if opts.empty:
        return None

    atm_guess = round(ltp / step) * step
    opts["dist"] = (opts["strike_val"] - atm_guess).abs()
    row = opts.sort_values("dist").iloc[0]

    return {
        "token": str(row["token"]),
        "tradingsymbol": row["symbol"],
        "strike": row["strike_val"],
        "expiry": nearest_expiry.strftime("%d-%b-%Y"),
    }


# ============================================================
# OPTION'S OWN VOLUME PROFILE (short recent history)
# ============================================================

def fetch_recent_data(smart_api, token, days=5, interval="FIFTEEN_MINUTE"):
    end = datetime.now()
    start = end - timedelta(days=days)
    resp = smart_api.getCandleData({
        "exchange": "NFO", "symboltoken": token, "interval": interval,
        "fromdate": start.strftime("%Y-%m-%d 09:15"),
        "todate": end.strftime("%Y-%m-%d 15:30"),
    })
    rows = resp.get("data") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def calculate_volume_profile(df, num_bins=24, value_area_pct=0.70):
    if df.empty:
        return None
    price_min, price_max = df["low"].min(), df["high"].max()
    if price_max <= price_min:
        return None

    tick = 0.05
    total_ticks = max(1, round((price_max - price_min) / tick))
    ticks_per_row = max(1, round(total_ticks / num_bins))
    bin_edges = [price_min]
    remaining, px = total_ticks, price_min
    while remaining > 0:
        tt = min(ticks_per_row, remaining)
        px += tt * tick
        bin_edges.append(px)
        remaining -= tt
    bin_edges = np.array(bin_edges)
    actual_bins = len(bin_edges) - 1
    bin_volumes = np.zeros(actual_bins)

    for _, row in df.iterrows():
        low, high, vol = row["low"], row["high"], row["volume"]
        if vol <= 0 or high <= low:
            continue
        start_bin = max(0, min(np.searchsorted(bin_edges, low, side="right") - 1, actual_bins - 1))
        end_bin = max(0, min(np.searchsorted(bin_edges, high, side="right") - 1, actual_bins - 1))
        span = end_bin - start_bin + 1
        bin_volumes[start_bin:end_bin + 1] += vol / span

    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    total_volume = bin_volumes.sum()
    if total_volume == 0:
        return None

    poc_idx = int(np.argmax(bin_volumes))
    target_volume = total_volume * value_area_pct
    cum_volume = bin_volumes[poc_idx]
    low_i, high_i = poc_idx, poc_idx

    while cum_volume < target_volume and (low_i > 0 or high_i < actual_bins - 1):
        vol_below = bin_volumes[low_i - 1] if low_i > 0 else -1
        vol_above = bin_volumes[high_i + 1] if high_i < actual_bins - 1 else -1
        if vol_above >= vol_below:
            high_i += 1
            cum_volume += bin_volumes[high_i]
        else:
            low_i -= 1
            cum_volume += bin_volumes[low_i]

    return {
        "poc": round(float(bin_centers[poc_idx]), 2),
        "vah": round(float(bin_centers[high_i]), 2),
        "val": round(float(bin_centers[low_i]), 2),
        "ltp": round(float(df["close"].iloc[-1]), 2),
    }


def option_sl_target(levels):
    """Buyer of the option always wants premium to rise. Nearest fib above LTP = target,
    nearest below = SL, using VAL-VAH range for the fib ladder."""
    if levels is None:
        return None
    ltp, val, vah = levels["ltp"], levels["val"], levels["vah"]
    rng = vah - val
    if rng <= 0:
        return None
    fib_prices = sorted(set(round(val + rng * r, 2) for r in FIB_RATIOS if val + rng * r > 0))
    above = [f for f in fib_prices if f > ltp]
    below = [f for f in fib_prices if f < ltp]
    return {
        "target": above[0] if above else None,
        "sl": below[-1] if below else round(ltp * 0.7, 2),
    }


def main():
    smart_api = login()
    master_df = load_master()

    result = {"generated_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(), "indexes": []}

    for idx in INDEXES:
        entry = {"name": idx["name"]}
        rng = prev_day_range(smart_api, idx["token"])
        if not rng:
            entry["error"] = "gai kali no data na malyo"
            result["indexes"].append(entry)
            continue

        lo, hi = rng["low"], rng["high"]
        entry["prev_day"] = rng

        close_sig = nearest_level(rng["close"], lo, hi)
        entry["close_signal"] = close_sig

        time.sleep(0.5)
        ltp = get_ltp(smart_api, idx["token"], idx["name"])
        entry["ltp"] = ltp
        today_sig = nearest_level(ltp, lo, hi) if ltp else None
        entry["today_signal"] = today_sig

        active_sig = today_sig or close_sig
        entry["option"] = None

        if active_sig and active_sig["action"] in ("CALL", "PUT"):
            opt_type = "CE" if active_sig["action"] == "CALL" else "PE"
            opt_name = "NIFTY" if idx["name"] == "NIFTY" else "BANKNIFTY"
            try:
                opt = find_atm_option(master_df, opt_name, ltp or rng["close"], idx["step"], opt_type)
            except Exception as e:
                opt = None
                entry["option_error"] = f"strike shodhta error: {e}"

            if opt:
                time.sleep(0.5)
                try:
                    odf = fetch_recent_data(smart_api, opt["token"])
                    levels = calculate_volume_profile(odf)
                    st = option_sl_target(levels)
                except Exception as e:
                    levels, st = None, None
                    entry["option_error"] = f"option data error: {e}"

                entry["option"] = {
                    "symbol": opt["tradingsymbol"],
                    "strike": opt["strike"],
                    "expiry": opt["expiry"],
                    "type": opt_type,
                    "levels": levels,
                    "sl_target": st,
                }
            else:
                entry["option_error"] = entry.get("option_error", "matching option na malyu")

        result["indexes"].append(entry)
        time.sleep(0.5)

    with open(OUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Done. Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()
