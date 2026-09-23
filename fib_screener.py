"""
Fib + Signal Screener (New)
Runs on GitHub Actions.
Fetches 52-week 30-min candles from Angel One,
calculates Volume Profile + Fibonacci levels with custom labels,
applies strength / stall / reversal logic,
and sends Telegram alerts.
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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
RESULTS_FILE = "fib_results.json"

SYMBOL_LIST = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "NESTLEIND", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
    "TATACONSUMER", "TATAMOTORS", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

INTERVAL = "THIRTY_MINUTE"
WEEKS_LOOKBACK = 52
CHUNK_DAYS = 28
VALUE_AREA_PCT = 0.70
NUM_BINS = 24
API_DELAY_SEC = 0.4
CONFLUENCE_TOLERANCE_PCT = 0.5

# Custom Fib levels (exact labels as discussed)
FIB_LEVELS = [
    (1.618, "GOLDEN REVERSAL / Extension"),
    (1.272, "STALL ZONE FOR BREAKOUTS"),
    (1.000, "BASE MOVE"),
    (0.786, "Pre-breakout stall"),
    (0.618, "GOLDEN REVERSAL"),
    (0.500, "Mid / Balance"),
    (0.382, "Stronger reaction zone"),
    (0.236, "REVERSAL"),
    (0.000, "BASE MOVE / Demand"),
    (-0.272, "Day Low/High zone"),
    (-1.618, "IMPULSIVE TARGET"),
]

def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    return smart_api

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials missing. Skipping alert.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}")

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
            "exchange": "NSE",
            "symboltoken": token,
            "interval": INTERVAL,
            "fromdate": chunk_start.strftime("%Y-%m-%d 09:15"),
            "todate": chunk_end.strftime("%Y-%m-%d 15:30"),
        }
        for attempt in range(3):
            try:
                response = smart_api.getCandleData(params)
                if response.get("status") and response.get("data"):
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
        "recent_low": round(float(df["low"].tail(20).min()), 2),
    }

def calculate_fib_levels(val, vah):
    rng = vah - val
    if rng <= 0:
        return []
    levels = []
    for ratio, name in FIB_LEVELS:
        price = round(val + ratio * rng, 2)
        if price > 0:
            levels.append({"ratio": ratio, "name": name, "price": price})
    return levels

def analyze_signal(ltp, recent_low, poc, vah, val, fib_levels):
    tolerance = ltp * (CONFLUENCE_TOLERANCE_PCT / 100)

    # 0.236 strength check
    level_0236 = next((f for f in fib_levels if f["ratio"] == 0.236), None)
    strength = False
    if level_0236 and recent_low > level_0236["price"]:
        strength = True

    # Near key levels
    near_stall = any(abs(f["price"] - ltp) <= tolerance and f["ratio"] == 1.272 for f in fib_levels)
    near_golden = any(abs(f["price"] - ltp) <= tolerance and f["ratio"] in (0.618, 1.618) for f in fib_levels)
    near_base = any(abs(f["price"] - ltp) <= tolerance and f["ratio"] in (0.0, 1.0) for f in fib_levels)

    bias = "Bullish" if ltp > poc else "Bearish"

    # Signal text
    notes = []
    if strength:
        notes.append("Strength (Low above 0.236)")
    if near_stall:
        notes.append("At STALL ZONE (1.272)")
    if near_golden:
        notes.append("At GOLDEN REVERSAL")
    if near_base:
        notes.append("At BASE MOVE")

    if not notes:
        notes.append("No major Fib confluence")

    signal = " | ".join(notes)

    # Simple targets / SL
    sorted_fib = sorted(fib_levels, key=lambda x: x["price"])
    above = [f for f in sorted_fib if f["price"] > ltp + tolerance]
    below = [f for f in sorted_fib if f["price"] < ltp - tolerance]

    if bias == "Bullish":
        target = above[0]["price"] if above else None
        sl = below[-1]["price"] if below else None
    else:
        target = below[-1]["price"] if below else None
        sl = above[0]["price"] if above else None

    return {
        "bias": bias,
        "signal": signal,
        "strength": strength,
        "near_stall": near_stall,
        "near_golden": near_golden,
        "target": target,
        "stop_loss": sl,
    }

def main():
    smart_api = login()
    token_map = build_symbol_token_map(SYMBOL_LIST)
    results = []
    alerts = []

    for symbol, token in token_map.items():
        try:
            df = fetch_52_week_data(smart_api, token)
            levels = calculate_volume_profile(df)
            if levels is None:
                continue

            fib_levels = calculate_fib_levels(levels["VAL"], levels["VAH"])
            signal_info = analyze_signal(
                levels["LTP"], levels["recent_low"],
                levels["POC"], levels["VAH"], levels["VAL"], fib_levels
            )

            row = {
                "symbol": symbol,
                "ltp": levels["LTP"],
                "poc": levels["POC"],
                "vah": levels["VAH"],
                "val": levels["VAL"],
                "recent_low": levels["recent_low"],
                **signal_info,
            }
            results.append(row)

            # Telegram alert only for interesting setups
            if signal_info["strength"] or signal_info["near_stall"] or signal_info["near_golden"]:
                msg = (
                    f"<b>{symbol}</b>\n"
                    f"LTP: {levels['LTP']}\n"
                    f"Bias: {signal_info['bias']}\n"
                    f"Signal: {signal_info['signal']}\n"
                    f"Target: {signal_info['target']}\n"
                    f"SL: {signal_info['stop_loss']}"
                )
                alerts.append(msg)

        except Exception as e:
            print(f"[ERROR] {symbol}: {e}")

    # Save results
    output = {
        "last_updated": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        "stocks": results,
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done. {len(results)} stocks saved to {RESULTS_FILE}")

    # Send Telegram alerts
    if alerts:
        header = f"<b>Fib Screener Alerts</b>\n{datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d-%b %H:%M')}\n\n"
        full_msg = header + "\n\n".join(alerts[:15])  # limit to 15 to avoid too long message
        send_telegram(full_msg)
        print(f"Sent {len(alerts)} alerts to Telegram")
    else:
        print("No strong Fib signals today")

if __name__ == "__main__":
    main()
