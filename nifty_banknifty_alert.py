"""
Pre-market Nifty & BankNifty options table.
Har index mate CE-ATM, CE-ITM, PE-ATM, PE-ITM — chaare options no potano
data (POC/VAH/VAL, named levels) ane Greeks (delta/theta). Index no potano
badho named level ladder pan generate thay chhe.
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
from SmartApi.smartExceptions import DataException

API_KEY = os.environ["ANGEL_API_KEY"]
CLIENT_CODE = os.environ["ANGEL_CLIENT_ID"]
PASSWORD = os.environ["ANGEL_PASSWORD"]
TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"]

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

INDEXES = [
    {"name": "NIFTY", "opt_name": "NIFTY", "token": "99926000", "step": 50},
    {"name": "BANKNIFTY", "opt_name": "BANKNIFTY", "token": "99926009", "step": 100},
]

BIAS_LEVELS = [
    (0.0,   "Base Move (bottom)", "PUT"),
    (0.25,  "Buy Reversal",       "CALL"),
    (0.75,  "Sell Reversal",      "PUT"),
    (1.0,   "Base Move (top)",    "CALL"),
    (1.272, "Stall Zone",         "WATCH"),
]

FIB_RATIOS_NAMED = [
    (1.618, "Golden Reversal"),
    (1.272, "Stall Zone"),
    (1.0,   "Base Move"),
    (0.786, "Deep Zone"),
    (0.618, "Reversal Zone"),
    (0.5,   "Mid Zone"),
    (0.382, "Reaction Zone"),
    (0.236, "Early Reversal"),
    (0.0,   "Base Move"),
    (-0.272, "Day Low Zone"),
    (-1.618, "Extreme Zone"),
]

OUT_FILE = "index_alert.json"


# ============================================================
# RATE-LIMIT SAFE WRAPPER
# Angel One SmartAPI historical/candle endpoints have a strict
# per-second/per-minute cap. Jo tame consecutive calls faster
# thi kariye to "Access denied because of exceeding access rate"
# error aave chhe. Aa wrapper e error par automatic retry +
# increasing wait karse.
# ============================================================

def safe_call(fn, *args, retries=5, base_delay=3, label="", **kwargs):
    """Call any smart_api function with retry + backoff on rate-limit errors."""
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except DataException as e:
            msg = str(e)
            if "exceeding access rate" in msg and attempt < retries:
                wait = base_delay * attempt  # 3s, 6s, 9s, 12s...
                print(f"[rate-limit] {label} attempt {attempt}/{retries} failed, retrying in {wait}s")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < retries:
                wait = base_delay * attempt
                print(f"[retry] {label} attempt {attempt}/{retries} error: {e}, retrying in {wait}s")
                time.sleep(wait)
            else:
                raise


def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    return smart_api


def fib(ratio, lo, hi):
    return lo + (hi - lo) * ratio


def full_ladder(lo, hi):
    return [
        {"name": name, "price": round(fib(ratio, lo, hi), 2)}
        for ratio, name in FIB_RATIOS_NAMED
        if fib(ratio, lo, hi) > 0
    ]


def prev_day_range(smart_api, token):
    end = datetime.now()
    start = end - timedelta(days=10)
    resp = safe_call(
        smart_api.getCandleData,
        {
            "exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
            "fromdate": start.strftime("%Y-%m-%d 09:15"),
            "todate": end.strftime("%Y-%m-%d 15:30"),
        },
        label=f"prev_day_range({token})",
    )
    rows = resp.get("data") or []
    if not rows:
        return None
    last = rows[-1]
    return {"date": last[0][:10], "high": float(last[2]), "low": float(last[3]), "close": float(last[4])}


def get_ltp(smart_api, token, name, exchange="NSE"):
    try:
        resp = safe_call(smart_api.ltpData, exchange, name, token, label=f"ltp({name})")
        if resp.get("status") and resp.get("data"):
            return float(resp["data"]["ltp"])
    except Exception as e:
        print(f"[WARN] LTP failed for {name}: {e}")
    return None


def nearest_bias(price, lo, hi):
    computed = sorted(
        [(ratio, label, action, fib(ratio, lo, hi)) for ratio, label, action in BIAS_LEVELS],
        key=lambda x: x[0],
    )
    best_idx = min(range(len(computed)), key=lambda i: abs(price - computed[i][3]))
    ratio, label, action, lvl_price = computed[best_idx]
    return {"ratio": ratio, "label": label, "action": action, "level_price": round(lvl_price, 2)}


# ============================================================
# OPTION CHAIN — nearest weekly expiry, ATM + one ITM strike
# ============================================================

def load_master():
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    return pd.DataFrame(resp.json())


def get_expiry_options(master_df, name):
    opts = master_df[
        (master_df["name"] == name)
        & (master_df["exch_seg"] == "NFO")
        & (master_df["instrumenttype"] == "OPTIDX")
    ].copy()
    if opts.empty:
        return None, None

    opts["expiry_dt"] = pd.to_datetime(opts["expiry"], format="%d%b%Y", errors="coerce")
    opts = opts.dropna(subset=["expiry_dt"])
    today = pd.Timestamp.now().normalize()
    upcoming = opts[opts["expiry_dt"] >= today]
    if upcoming.empty:
        return None, None
    nearest_expiry = upcoming["expiry_dt"].min()
    opts = upcoming[upcoming["expiry_dt"] == nearest_expiry].copy()
    opts["strike_val"] = pd.to_numeric(opts["strike"], errors="coerce") / 100.0
    opts = opts.dropna(subset=["strike_val"])
    return nearest_expiry, opts


def pick_strikes(opts_df, spot):
    strikes = sorted(opts_df["strike_val"].unique())
    if not strikes:
        return None, None, None
    atm = min(strikes, key=lambda s: abs(s - spot))
    idx = strikes.index(atm)
    itm_ce = strikes[idx - 1] if idx > 0 else atm
    itm_pe = strikes[idx + 1] if idx < len(strikes) - 1 else atm
    return atm, itm_ce, itm_pe


def find_row(opts_df, strike, opt_type):
    m = opts_df[(opts_df["strike_val"].round(2) == round(strike, 2)) & (opts_df["symbol"].str.endswith(opt_type))]
    if m.empty:
        return None
    r = m.iloc[0]
    return {"token": str(r["token"]), "symbol": r["symbol"], "strike": strike}


def fetch_greeks(smart_api, opt_name, expiry_dt):
    try:
        expiry_str = expiry_dt.strftime("%d%b%Y").upper()
        resp = safe_call(
            smart_api.optionGreek,
            {"name": opt_name, "expirydate": expiry_str},
            label=f"greeks({opt_name})",
        )
        rows = resp.get("data") or []
        print(f"[DEBUG] greeks for {opt_name}: status={resp.get('status')} rows={len(rows)} sample={rows[:1]}")
        out = {}
        for r in rows:
            try:
                k = (round(float(r.get("strikePrice")), 2), r.get("optionType"))
                out[k] = {"delta": r.get("delta"), "theta": r.get("theta")}
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"[WARN] greeks failed for {opt_name}: {e}")
        return {}


def get_greek(greeks_map, strike, opt_type):
    key = (round(strike, 2), opt_type)
    g = greeks_map.get(key)
    if g:
        return g
    for (s, t), v in greeks_map.items():
        if t == opt_type and abs(s - strike) < 1:
            return v
    return {"delta": None, "theta": None}


# ============================================================
# OPTION'S OWN VOLUME PROFILE (recent history)
# ============================================================

def fetch_recent_data(smart_api, token, days=5, interval="FIFTEEN_MINUTE"):
    end = datetime.now()
    start = end - timedelta(days=days)
    resp = safe_call(
        smart_api.getCandleData,
        {
            "exchange": "NFO", "symboltoken": token, "interval": interval,
            "fromdate": start.strftime("%Y-%m-%d 09:15"),
            "todate": end.strftime("%Y-%m-%d 15:30"),
        },
        label=f"recent_data({token})",
    )
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


def option_nearest_zone(levels):
    if levels is None:
        return None
    ltp, val, vah = levels["ltp"], levels["val"], levels["vah"]
    rng = vah - val
    if rng <= 0:
        return None
    ladder = sorted(
        [(round(val + rng * r, 2), nm) for r, nm in FIB_RATIOS_NAMED if val + rng * r > 0],
        key=lambda x: x[0],
    )
    if not ladder:
        return None
    nearest = min(ladder, key=lambda x: abs(x[0] - ltp))
    return {"near_name": nearest[1], "near_price": nearest[0]}


def build_option(smart_api, opts_df, strike, opt_type, moneyness, greeks_map):
    row = find_row(opts_df, strike, opt_type)
    if not row:
        return {"type": opt_type, "moneyness": moneyness, "strike": strike, "error": "strike na malyo"}

    time.sleep(1)
    odf = fetch_recent_data(smart_api, row["token"])
    levels = calculate_volume_profile(odf)
    nz = option_nearest_zone(levels)
    greek = get_greek(greeks_map, strike, opt_type)

    return {
        "type": opt_type, "moneyness": moneyness, "symbol": row["symbol"], "strike": strike,
        "ltp": levels["ltp"] if levels else None,
        "delta": greek.get("delta"), "theta": greek.get("theta"),
        "near_name": nz["near_name"] if nz else None,
        "near_price": nz["near_price"] if nz else None,
    }


def main():
    smart_api = login()
    master_df = load_master()
    today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()

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
        entry["levels"] = full_ladder(lo, hi)
        entry["close_signal"] = nearest_bias(rng["close"], lo, hi)

        time.sleep(1)
        ltp = get_ltp(smart_api, idx["token"], idx["name"])
        entry["ltp"] = ltp
        entry["today_signal"] = nearest_bias(ltp, lo, hi) if ltp else None

        spot = ltp or rng["close"]

        try:
            expiry_dt, opts_df = get_expiry_options(master_df, idx["opt_name"])
        except Exception as e:
            expiry_dt, opts_df = None, None
            entry["option_error"] = f"expiry shodhta error: {e}"

        entry["options"] = []
        if expiry_dt is not None and opts_df is not None and not opts_df.empty:
            entry["expiry"] = expiry_dt.strftime("%d-%b-%Y")
            entry["is_expiry_day"] = (expiry_dt.date() == today_ist)

            atm, itm_ce, itm_pe = pick_strikes(opts_df, spot)

            time.sleep(1)
            greeks_map = fetch_greeks(smart_api, idx["opt_name"], expiry_dt)

            for strike, opt_type, money in [
                (atm, "CE", "ATM"), (itm_ce, "CE", "ITM"),
                (atm, "PE", "ATM"), (itm_pe, "PE", "ITM"),
            ]:
                try:
                    entry["options"].append(build_option(smart_api, opts_df, strike, opt_type, money, greeks_map))
                except Exception as e:
                    entry["options"].append({"type": opt_type, "moneyness": money, "strike": strike, "error": str(e)})
        else:
            entry["option_error"] = entry.get("option_error", "options na malya")

        result["indexes"].append(entry)
        time.sleep(1.5)

    with open(OUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Done. Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()
