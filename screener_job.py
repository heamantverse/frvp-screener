"""
Daily screener job.
Runs on GitHub Actions: fetches 52-week 30-minute candles from Angel One,
computes volume-profile levels, Fibonacci levels and extra indicators,
and writes results.json.
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

# ============================================================
# CONFIG
# ============================================================
API_KEY = os.environ["ANGEL_API_KEY"]
CLIENT_CODE = os.environ["ANGEL_CLIENT_ID"]
PASSWORD = os.environ["ANGEL_PASSWORD"]
TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"]

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
NSE_MASTER_CSV = "nse_equity_master.csv"
RESULTS_FILE = "results.json"

ALL_STOCKS = False
SYMBOL_LIST = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUMER", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

INTERVAL = "THIRTY_MINUTE"
WEEKS_LOOKBACK = 52
CHUNK_DAYS = 28
VALUE_AREA_PCT = 0.70
NUM_BINS = 24
API_DELAY_SEC = 0.4
CONFLUENCE_TOLERANCE_PCT = 0.5

# Extra indicators
LOW_LIQUIDITY_CR = 5        # avg daily traded value below this (Rs crore) = "Low"
CORP_ACTION_GAP = 0.25      # single-day gap above 25% = possible split/bonus
NIFTY_TOKEN = "99926000"    # Nifty 50 index token on Angel One


def _num(name):
    try:
        return float(os.environ.get(name, "") or 0)
    except ValueError:
        return 0.0


# Optional (set as GitHub Secrets). If missing, Qty is left blank.
CAPITAL = _num("CAPITAL")
RISK_PCT = _num("RISK_PCT")

FIB_LEVELS = [
    (1.618, "Extension target (breakout hold kare to)"),
    (1.272, "Extension target (breakout hold kare to)"),
    (1.000, "Range nu top / supply (=VAH)"),
    (0.786, "Breakout pehla stall"),
    (0.618, "Stronger reaction zone"),
    (0.500, "Mid / balance"),
    (0.382, "Stronger reaction zone"),
    (0.236, "Pehli weak bounce"),
    (0.000, "Range nu bottom / demand side (=VAL)"),
    (-0.272, "Day Low/Day High zone"),
    (-1.618, "Impulsive Target"),
]


def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    return smart_api


def build_symbol_token_map(symbols):
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    master_df = pd.DataFrame(resp.json())
    nse_eq = master_df[(master_df["exch_seg"] == "NSE") & (master_df["symbol"].str.endswith("-EQ"))]
    token_map = {}
    for sym in symbols:
        match = nse_eq[nse_eq["symbol"] == f"{sym}-EQ"]
        if not match.empty:
            token_map[sym] = str(match.iloc[0]["token"])
    return token_map


def fetch_52_week_data(smart_api, token):
    end = datetime.now()
    start = end - timedelta(weeks=WEEKS_LOOKBACK)
    all_chunks = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end)
        params = {
            "exchange": "NSE", "symboltoken": token, "interval": INTERVAL,
            "fromdate": chunk_start.strftime("%Y-%m-%d 09:15"),
            "todate": chunk_end.strftime("%Y-%m-%d 15:30"),
        }
        for attempt in range(3):
            try:
                response = smart_api.getCandleData(params)
                if response.get("status"):
                    if response.get("data"):
                        all_chunks.extend(response["data"])
                    break
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        chunk_start = chunk_end
        time.sleep(API_DELAY_SEC)

    if not all_chunks:
        return pd.DataFrame()
    df = pd.DataFrame(all_chunks, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    # chunk boundaries can repeat a candle; remove duplicates
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def calculate_volume_profile(df, num_bins=NUM_BINS, value_area_pct=VALUE_AREA_PCT):
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
        "POC": round(float(bin_centers[poc_idx]), 2),
        "VAH": round(float(bin_centers[high_i]), 2),
        "VAL": round(float(bin_centers[low_i]), 2),
        "LTP": round(float(df["close"].iloc[-1]), 2),
        "week52_high": round(float(price_max), 2),
        "week52_low": round(float(price_min), 2),
    }


def calculate_fib_levels(week52_low, week52_high):
    rng = week52_high - week52_low
    levels = []
    for ratio, name in FIB_LEVELS:
        price = round(week52_low + ratio * rng, 2)
        if price > 0:
            levels.append({"ratio": ratio, "name": name, "price": price})
    return levels


def analyze_signal(ltp, poc, vah, val, fib_levels):
    bias = "Bullish" if ltp > poc else "Bearish"
    tolerance = ltp * (CONFLUENCE_TOLERANCE_PCT / 100)
    near_fib = [f for f in fib_levels if abs(f["price"] - ltp) <= tolerance]
    near_va = abs(ltp - vah) <= tolerance or abs(ltp - val) <= tolerance
    confluence = bool(near_fib) and near_va

    sorted_prices = sorted(fib_levels, key=lambda f: f["price"])
    strictly_above = [f for f in sorted_prices if f["price"] > ltp + tolerance]
    strictly_below = [f for f in sorted_prices if f["price"] < ltp - tolerance]

    if bias == "Bullish":
        short_target = strictly_above[0] if strictly_above else None
        long_target = strictly_above[-1] if strictly_above else None
        sl_level = strictly_below[-1] if strictly_below else None
    else:
        short_target = strictly_below[-1] if strictly_below else None
        long_target = strictly_below[0] if strictly_below else None
        sl_level = strictly_above[0] if strictly_above else None

    signal = "WATCH - Confluence Zone" if confluence else ("BUY bias" if bias == "Bullish" else "SELL bias")
    return {
        "bias": bias, "signal": signal,
        "confluence": confluence,
        "short_target": short_target["price"] if short_target else None,
        "long_target": long_target["price"] if long_target else None,
        "stop_loss": sl_level["price"] if sl_level else None,
    }


def trend_label(ltp, dma50, dma200):
    if dma50 is None or dma200 is None:
        return "NA"
    if ltp > dma200 and dma50 > dma200:
        return "Up"
    if ltp < dma200 and dma50 < dma200:
        return "Down"
    return "Mixed"


def calculate_extras(df):
    d = df.sort_values("timestamp").copy()
    d["date"] = d["timestamp"].astype(str).str[:10]
    daily = d.groupby("date", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).reset_index()

    n = len(daily)
    closes = daily["close"]
    ltp = float(closes.iloc[-1])
    dma50 = float(closes.tail(50).mean()) if n >= 50 else None
    dma200 = float(closes.tail(200).mean()) if n >= 200 else None

    vol_ratio = None
    if n >= 21:
        avg20 = float(daily["volume"].iloc[-21:-1].mean())
        if avg20 > 0:
            vol_ratio = round(float(daily["volume"].iloc[-1]) / avg20, 2)

    avg_value_cr = round(float((daily["close"] * daily["volume"]).tail(20).mean()) / 1e7, 1)

    corp_action = False
    if n >= 2:
        gaps = daily["open"].iloc[1:].values / daily["close"].iloc[:-1].values - 1
        corp_action = bool((np.abs(gaps) > CORP_ACTION_GAP).any())

    return {
        "trend": trend_label(ltp, dma50, dma200),
        "vol_ratio": vol_ratio,
        "avg_value_cr": avg_value_cr,
        "liquidity": "Low" if avg_value_cr < LOW_LIQUIDITY_CR else "OK",
        "corp_action": corp_action,
    }


def risk_reward(ltp, sig):
    target, sl = sig["short_target"], sig["stop_loss"]
    if target is None or sl is None:
        return None, None
    if sig["bias"] == "Bullish":
        reward, risk = target - ltp, ltp - sl
    else:
        reward, risk = ltp - target, sl - ltp
    if reward <= 0 or risk <= 0:
        return None, None
    rr = round(reward / risk, 2)
    qty = None
    if CAPITAL > 0 and RISK_PCT > 0:
        qty = int(min((CAPITAL * RISK_PCT / 100) / risk, CAPITAL / ltp))
    return rr, qty


def nifty_status(smart_api):
    try:
        end = datetime.now()
        start = end - timedelta(days=420)
        resp = smart_api.getCandleData({
            "exchange": "NSE", "symboltoken": NIFTY_TOKEN, "interval": "ONE_DAY",
            "fromdate": start.strftime("%Y-%m-%d 09:15"),
            "todate": end.strftime("%Y-%m-%d 15:30"),
        })
        rows = resp.get("data") or []
        closes = [float(r[4]) for r in rows]
        if len(closes) < 200:
            return None
        ltp = closes[-1]
        dma50 = sum(closes[-50:]) / 50
        dma200 = sum(closes[-200:]) / 200
        return {
            "trend": trend_label(ltp, dma50, dma200),
            "close": round(ltp, 2),
            "dma200": round(dma200, 2),
        }
    except Exception as e:
        print(f"[WARN] Nifty status failed: {e}")
        return None


def main():
    smart_api = login()

    if ALL_STOCKS:
        master_df = pd.read_csv(NSE_MASTER_CSV)
        symbols = master_df["symbol"].tolist()
    else:
        symbols = SYMBOL_LIST

    token_map = build_symbol_token_map(symbols)
    results = []

    for symbol, token in token_map.items():
        try:
            df = fetch_52_week_data(smart_api, token)
            levels = calculate_volume_profile(df)
            if levels is None:
                continue
            fib_levels = calculate_fib_levels(levels["VAL"], levels["VAH"])
            signal_info = analyze_signal(levels["LTP"], levels["POC"], levels["VAH"], levels["VAL"], fib_levels)
            extras = calculate_extras(df)
            rr, qty = risk_reward(levels["LTP"], signal_info)
            ltp = levels["LTP"]
            results.append({
                "symbol": symbol,
                "ltp": ltp,
                "poc": levels["POC"],
                "vah": levels["VAH"],
                "val": levels["VAL"],
                "week52_high": levels["week52_high"],
                "week52_low": levels["week52_low"],
                **signal_info,
                **extras,
                "from_high_pct": round((ltp / levels["week52_high"] - 1) * 100, 1),
                "from_low_pct": round((ltp / levels["week52_low"] - 1) * 100, 1),
                "rr": rr,
                "qty": qty,
            })
        except Exception as e:
            print(f"[ERROR] {symbol}: {e}")

    output = {
        "last_updated": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        "market": nifty_status(smart_api),
        "stocks": results,
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done. {len(results)} stocks saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
