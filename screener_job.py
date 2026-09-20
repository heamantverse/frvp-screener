"""
Daily Screener Job (PythonAnywhere Scheduled Task તરીકે run થશે)
====================================================================
આ ફાઇલ રોજ રાત્રે automatically run થશે (PythonAnywhere ના "Scheduled
Tasks" દ્વારા), બધા stocks નો 52-week FRVP + Fibonacci data ભેગો કરી
results.json માં save કરશે. Website (app.py) એ ફાઇલ વાંચીને બતાવશે.

SETUP:
1. PythonAnywhere પર આ ફાઇલ, app.py, templates/index.html,
   nse_equity_master.csv - બધું upload કરો (Files tab).
2. Bash console ખોલી: pip3.10 install --user smartapi-python pyotp
   flask logzero websocket-client pandas numpy requests
3. નીચે CONFIG માં Angel One credentials ભરો.
4. "Tasks" tab માં નવું Scheduled Task બનાવો:
   Command: python3.10 /home/<username>/frvp_website/screener_job.py
   Time: તમને ફાવે એ (market બંધ થયા પછી, દા.ત. 18:00 UTC = 23:30 IST)
"""

import time
import json
import numpy as np
import pandas as pd
import requests
import pyotp
from datetime import datetime, timedelta
from SmartApi import SmartConnect

# ============================================================
# CONFIG
# ============================================================
API_KEY = "YOUR_ANGEL_API_KEY"
CLIENT_CODE = "YOUR_CLIENT_CODE"
PASSWORD = "YOUR_PIN"
TOTP_SECRET = "YOUR_TOTP_SECRET"

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
NSE_MASTER_CSV = "/home/YOUR_USERNAME/frvp_website/nse_equity_master.csv"  # PythonAnywhere path
RESULTS_FILE = "/home/YOUR_USERNAME/frvp_website/results.json"

# ALL_STOCKS=True કરતા પહેલા, free tier ના daily task ના time-limit ને
# ધ્યાનમાં રાખી પહેલા 100-300 stocks થી શરૂ કરવાની સલાહ છે
ALL_STOCKS = False
SYMBOL_LIST = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
               "SBIN", "AXISBANK", "BHARTIARTL", "ITC"]

INTERVAL = "THIRTY_MINUTE"
WEEKS_LOOKBACK = 52
CHUNK_DAYS = 28
VALUE_AREA_PCT = 0.70
NUM_BINS = 24
API_DELAY_SEC = 0.4
CONFLUENCE_TOLERANCE_PCT = 0.5

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
        try:
            response = smart_api.getCandleData(params)
            if response.get("status") and response.get("data"):
                all_chunks.extend(response["data"])
        except Exception:
            pass
        chunk_start = chunk_end
        time.sleep(API_DELAY_SEC)

    if not all_chunks:
        return pd.DataFrame()
    df = pd.DataFrame(all_chunks, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
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
            results.append({
                "symbol": symbol,
                "ltp": levels["LTP"],
                "poc": levels["POC"],
                "vah": levels["VAH"],
                "val": levels["VAL"],
                "week52_high": levels["week52_high"],
                "week52_low": levels["week52_low"],
                **signal_info,
            })
        except Exception as e:
            print(f"[ERROR] {symbol}: {e}")

    output = {
        "last_updated": datetime.now().isoformat(),
        "stocks": results,
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done. {len(results)} stocks saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
