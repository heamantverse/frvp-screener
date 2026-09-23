"""
Intraday Fib Open Alert – Nifty + BankNifty
Previous day 5-min Volume Profile + Fib
Alert within ~5 mins of market open
Includes O/H/L + chart-pattern based prediction
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

NIFTY_TOKEN = "99926000"
BANKNIFTY_TOKEN = "99926009"

INTERVAL = "FIVE_MINUTE"
VALUE_AREA_PCT = 0.70
NUM_BINS = 24
TOLERANCE_PCT = 0.20          # 0.20% tolerance for "near" level

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

IST = ZoneInfo("Asia/Kolkata")

def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    return smart_api

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials missing")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[WARN] Telegram failed: {e}")

def get_previous_trading_day():
    today = datetime.now(IST).date()
    prev = today - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev

def fetch_previous_day_5min(smart_api, token, day):
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": INTERVAL,
        "fromdate": day.strftime("%Y-%m-%d 09:15"),
        "todate": day.strftime("%Y-%m-%d 15:30"),
    }
    for attempt in range(3):
        try:
            resp = smart_api.getCandleData(params)
            if resp.get("status") and resp.get("data"):
                df = pd.DataFrame(resp["data"], columns=["timestamp", "open", "high", "low", "close", "volume"])
                df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
                return df
        except Exception:
            pass
        time.sleep(1.5)
    return pd.DataFrame()

def fetch_opening_candle(smart_api, token):
    """First 5-min candle of today → Open, High, Low, Close"""
    today = datetime.now(IST).date()
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": INTERVAL,
        "fromdate": today.strftime("%Y-%m-%d 09:15"),
        "todate": today.strftime("%Y-%m-%d 09:25"),
    }
    for attempt in range(6):
        try:
            resp = smart_api.getCandleData(params)
            if resp.get("status") and resp.get("data"):
                row = resp["data"][0]
                return {
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                }
        except Exception:
            pass
        time.sleep(2)
    return None

def calculate_volume_profile(df):
    if df.empty:
        return None
    price_min, price_max = df["low"].min(), df["high"].max()
    if price_max <= price_min:
        return None

    tick = 0.05
    total_ticks = max(1, round((price_max - price_min) / tick))
    ticks_per_row = max(1, round(total_ticks / NUM_BINS))
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
        span = max(1, end_bin - start_bin + 1)
        bin_volumes[start_bin:end_bin + 1] += vol / span

    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    total_volume = bin_volumes.sum()
    if total_volume == 0:
        return None

    poc_idx = int(np.argmax(bin_volumes))
    target = total_volume * VALUE_AREA_PCT
    cum = bin_volumes[poc_idx]
    low_i = high_i = poc_idx

    while cum < target and (low_i > 0 or high_i < actual_bins - 1):
        vol_below = bin_volumes[low_i - 1] if low_i > 0 else -1
        vol_above = bin_volumes[high_i + 1] if high_i < actual_bins - 1 else -1
        if vol_above >= vol_below:
            high_i += 1
            cum += bin_volumes[high_i]
        else:
            low_i -= 1
            cum += bin_volumes[low_i]

    return {
        "POC": round(float(bin_centers[poc_idx]), 2),
        "VAH": round(float(bin_centers[high_i]), 2),
        "VAL": round(float(bin_centers[low_i]), 2),
        "day_high": round(float(price_max), 2),
        "day_low": round(float(price_min), 2),
    }

def calculate_fib_levels(val, vah):
    rng = vah - val
    if rng <= 0:
        return []
    return [{"ratio": r, "name": n, "price": round(val + r * rng, 2)} for r, n in FIB_LEVELS]

def get_level(fib_levels, ratio):
    for f in fib_levels:
        if abs(f["ratio"] - ratio) < 0.001:
            return f
    return None

def analyze(candle, fib_levels, poc):
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    tol = o * (TOLERANCE_PCT / 100)

    lvl_0236 = get_level(fib_levels, 0.236)
    lvl_0618 = get_level(fib_levels, 0.618)
    lvl_1000 = get_level(fib_levels, 1.000)
    lvl_1272 = get_level(fib_levels, 1.272)
    lvl_1618 = get_level(fib_levels, 1.618)

    strength = lvl_0236 and l > lvl_0236["price"]

    def near(price, level):
        return level and abs(price - level["price"]) <= tol

    notes = []
    prediction = []

    # Strength
    if strength:
        notes.append("✅ Strength (Low above 0.236)")
        prediction.append("Shallow pullback → uptrend continue chance high")

    # Open location
    if near(o, lvl_0618):
        notes.append("🔄 Open at GOLDEN REVERSAL (0.618)")
        prediction.append("Strong reaction zone. Possible early pause/reversal. Watch rejection or hold.")
    if near(o, lvl_1272):
        notes.append("⚠️ Open at STALL ZONE (1.272)")
        prediction.append("Decision zone. Breakout = continuation, Rejection = pullback.")
    if near(o, lvl_1618):
        notes.append("🔻 Open at GOLDEN REVERSAL Extension (1.618)")
        prediction.append("Exhaustion zone. High chance of reversal / profit booking.")
    if near(o, lvl_1000):
        notes.append("📌 Open at BASE MOVE (1.0)")
        prediction.append("Range extreme. Directional move expected soon.")

    # High / Low interaction
    if near(h, lvl_1272) or near(h, lvl_1618):
        notes.append("High touched Stall/Golden Extension")
        prediction.append("Watch for rejection from highs.")
    if near(l, lvl_0236) or near(l, lvl_0618):
        notes.append("Low tested Reversal / Golden zone")
        prediction.append("Support test – bounce chance if holds.")

    if not notes:
        notes.append("No major Fib confluence at open")
        prediction.append("Wait for clearer reaction at key levels.")

    bias = "Bullish" if o > poc else "Bearish"

    return {
        "bias": bias,
        "strength": strength,
        "notes": notes,
        "prediction": prediction,
    }

def process_index(smart_api, name, token, prev_day):
    df = fetch_previous_day_5min(smart_api, token, prev_day)
    if df.empty:
        return None

    vp = calculate_volume_profile(df)
    if vp is None:
        return None

    fib_levels = calculate_fib_levels(vp["VAL"], vp["VAH"])
    candle = fetch_opening_candle(smart_api, token)
    if candle is None:
        return None

    analysis = analyze(candle, fib_levels, vp["POC"])

    key = {}
    for r in [0.236, 0.618, 1.0, 1.272, 1.618]:
        lvl = get_level(fib_levels, r)
        if lvl:
            key[lvl["name"]] = lvl["price"]

    return {
        "name": name,
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
        "poc": vp["POC"],
        "vah": vp["VAH"],
        "val": vp["VAL"],
        "key_levels": key,
        **analysis,
    }

def main():
    now = datetime.now(IST)
    print(f"Running at {now.strftime('%Y-%m-%d %H:%M:%S')} IST")

    smart_api = login()
    prev_day = get_previous_trading_day()
    print(f"Previous trading day: {prev_day}")

    results = []
    for name, token in [("NIFTY", NIFTY_TOKEN), ("BANKNIFTY", BANKNIFTY_TOKEN)]:
        res = process_index(smart_api, name, token, prev_day)
        if res:
            results.append(res)

    if not results:
        send_telegram("❌ Fib Open Alert: No data")
        return

    lines = [f"<b>📊 Fib Open Alert</b>\n{now.strftime('%d-%b %H:%M')} IST\nPrevious Day: {prev_day}\n"]

    for r in results:
        lines.append(f"<b>{r['name']}</b>")
        lines.append(f"O: <b>{r['open']}</b> | H: {r['high']} | L: {r['low']}")
        lines.append(f"Bias: {r['bias']}")
        lines.append("Notes: " + " | ".join(r["notes"]))
        lines.append("Prediction:")
        for p in r["prediction"]:
            lines.append(f"• {p}")
        lines.append(f"POC: {r['poc']} | VAL: {r['val']} | VAH: {r['vah']}")
        lines.append(f"0.236: {r['key_levels'].get('REVERSAL', '-')}")
        lines.append(f"0.618: {r['key_levels'].get('GOLDEN REVERSAL', '-')}")
        lines.append(f"1.272: {r['key_levels'].get('STALL ZONE FOR BREAKOUTS', '-')}")
        lines.append(f"1.618: {r['key_levels'].get('GOLDEN REVERSAL / Extension', '-')}")
        lines.append("")

    msg = "\n".join(lines)
    send_telegram(msg)
    print("Alert sent")

    with open("fib_open_results.json", "w") as f:
        json.dump({"time": now.isoformat(), "results": results}, f, indent=2, default=str)

if __name__ == "__main__":
    main()
