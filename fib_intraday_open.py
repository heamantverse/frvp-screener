"""
Intraday Fib Open Alert – Nifty + BankNifty
Previous day High-Low range + Fib (index candles ma volume 0 hoy chhe,
etle volume-profile na badle sidhi high-low range vaparay chhe)
Alert within ~5 mins of market open
"""

import os
import time
import pandas as pd
import requests
import pyotp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from SmartApi import SmartConnect

print("=" * 60)
print("FIB INTRADAY OPEN ALERT STARTED")
print("=" * 60)

API_KEY = os.environ["ANGEL_API_KEY"]
CLIENT_CODE = os.environ["ANGEL_CLIENT_ID"]
PASSWORD = os.environ["ANGEL_PASSWORD"]
TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

NIFTY_TOKEN = "99926000"
BANKNIFTY_TOKEN = "99926009"
INTERVAL = "FIVE_MINUTE"
TOLERANCE_PCT = 0.20

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
    print("Logging in...")
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    print("Login successful")
    return smart_api

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram sent: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_previous_trading_day():
    today = datetime.now(IST).date()
    prev = today - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev

def fetch_previous_day_5min(smart_api, token, day):
    print(f"Fetching previous day data for token {token} | {day}")
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
                print(f"Got {len(df)} candles")
                return df
            else:
                print(f"Attempt {attempt+1}: No data in response → {resp}")
        except Exception as e:
            print(f"Attempt {attempt+1} error: {e}")
        time.sleep(1.5)
    print("Failed to fetch previous day data")
    return pd.DataFrame()

def fetch_opening_candle(smart_api, token):
    print(f"Fetching today's opening candle for token {token}")
    today = datetime.now(IST).date()
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": INTERVAL,
        "fromdate": today.strftime("%Y-%m-%d 09:15"),
        "todate": today.strftime("%Y-%m-%d 09:30"),
    }
    for attempt in range(8):
        try:
            resp = smart_api.getCandleData(params)
            if resp.get("status") and resp.get("data"):
                row = resp["data"][0]
                candle = {
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                }
                print(f"Opening candle: {candle}")
                return candle
            else:
                print(f"Attempt {attempt+1}: {resp}")
        except Exception as e:
            print(f"Attempt {attempt+1} error: {e}")
        time.sleep(3)
    print("Failed to fetch opening candle")
    return None

def range_from_prev_day(df):
    """Index candles ma volume 0 hoy chhe, etle high-low range j vaparay chhe."""
    if df.empty:
        print("Range debug: dataframe khali chhe")
        return None
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    print(f"Range debug: low={price_min} high={price_max}")
    if price_max <= price_min:
        print("Range debug: high <= low, fail")
        return None
    return {
        "POC": round((price_min + price_max) / 2, 2),
        "VAH": round(price_max, 2),
        "VAL": round(price_min, 2),
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
    o, h, l = candle["open"], candle["high"], candle["low"]
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

    if strength:
        notes.append("✅ Strength (Low above 0.236)")
        prediction.append("Shallow pullback → uptrend continue chance high")

    if near(o, lvl_0618):
        notes.append("🔄 Open at GOLDEN REVERSAL (0.618)")
        prediction.append("Strong reaction zone. Possible early pause/reversal.")
    if near(o, lvl_1272):
        notes.append("⚠️ Open at STALL ZONE (1.272)")
        prediction.append("Decision zone. Breakout = continuation, Rejection = pullback.")
    if near(o, lvl_1618):
        notes.append("🔻 Open at 1.618 Extension")
        prediction.append("Exhaustion zone. High chance of reversal.")
    if near(o, lvl_1000):
        notes.append("📌 Open at BASE MOVE")
        prediction.append("Range extreme. Directional move expected.")

    if not notes:
        notes.append("No major Fib confluence at open")
        prediction.append("Wait for clearer reaction at key levels.")

    bias = "Bullish" if o > poc else "Bearish"

    return {
        "bias": bias,
        "notes": notes,
        "prediction": prediction,
    }

def process_index(smart_api, name, token, prev_day):
    print(f"\n----- Processing {name} -----")
    df = fetch_previous_day_5min(smart_api, token, prev_day)
    if df.empty:
        print(f"{name}: Previous day data empty")
        return None

    vp = range_from_prev_day(df)
    if vp is None:
        print(f"{name}: Range calc failed")
        return None
    print(f"{name} Range → POC:{vp['POC']} VAL:{vp['VAL']} VAH:{vp['VAH']}")

    fib_levels = calculate_fib_levels(vp["VAL"], vp["VAH"])
    candle = fetch_opening_candle(smart_api, token)
    if candle is None:
        print(f"{name}: Opening candle failed")
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
        "poc": vp["POC"],
        "vah": vp["VAH"],
        "val": vp["VAL"],
        "key_levels": key,
        **analysis,
    }

def main():
    now = datetime.now(IST)
    print(f"Current time: {now}")

    smart_api = login()
    prev_day = get_previous_trading_day()
    print(f"Previous trading day: {prev_day}")

    results = []
    for name, token in [("NIFTY", NIFTY_TOKEN), ("BANKNIFTY", BANKNIFTY_TOKEN)]:
        res = process_index(smart_api, name, token, prev_day)
        if res:
            results.append(res)

    if not results:
        send_telegram("❌ Fib Open Alert: No data\nCheck logs for details")
        print("No results")
        return

    lines = [f"<b>📊 Fib Open Alert</b>\n{now.strftime('%d-%b %H:%M')} IST\nPrev Day: {prev_day}\n"]

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
    print("Full alert sent")

if __name__ == "__main__":
    main()
