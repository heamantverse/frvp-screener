"""
Intraday Fib Open Alert – Nifty + BankNifty + Sensex
Fakt e instrument process thay je nu manual VLOW/VHIGH Telegram par
moklyu hoy (website/Telegram → PythonAnywhere). Jena manual levels
nathi e instrument skip thai jay — auto previous-day fallback nathi.
Level names VRSuccessful indicator jeva j (website na FIB_RATIOS_NAMED
sathe match thay che)
Alert within ~5 mins of market open
"""

import os
import time
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

# Manual levels (website / Telegram thi PythonAnywhere par save thay che)
WEBSITE_URL = os.environ.get("WEBSITE_URL", "").rstrip("/")
FIB_API_SECRET = os.environ.get("FIB_API_SECRET", "")

INSTRUMENTS = [
    {"name": "NIFTY", "token": "99926000", "exchange": "NSE"},
    {"name": "BANKNIFTY", "token": "99926009", "exchange": "NSE"},
    {"name": "SENSEX", "token": "99919000", "exchange": "BSE"},
]

INTERVAL = "FIVE_MINUTE"
TOLERANCE_PCT = 0.20

# Same names/ratios as app.py (website) na FIB_RATIOS_NAMED — VRSuccessful indicator style
FIB_LEVELS = [
    (1.618, "Potential Target 2"),
    (1.272, "Potential Target 1"),
    (1.000, "Potential Break out"),
    (0.786, "Potential sell Reversal"),
    (0.618, "Reversal Zone"),
    (0.500, "Mid Zone"),
    (0.382, "Reaction Zone"),
    (0.236, "Potential Buy Reversal"),
    (0.000, "Break down"),
    (-0.272, "Potential Target 1"),
    (-1.618, "Potential Target 2"),
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

def fetch_manual_levels():
    """PythonAnywhere (website/Telegram) par save thayela VLOW/VHIGH levie.
    Failure/missing config hoy to khali dict pachu ave (koi instrument process nahi thay)."""
    if not WEBSITE_URL or not FIB_API_SECRET:
        print("Manual levels: WEBSITE_URL / FIB_API_SECRET set nathi, skipping")
        return {}
    url = f"{WEBSITE_URL}/fib/api/{FIB_API_SECRET}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            print(f"Manual levels fetched: {list(data.keys())}")
            return data
        print(f"Manual levels fetch failed: HTTP {r.status_code}")
    except Exception as e:
        print(f"Manual levels fetch error: {e}")
    return {}

def fetch_opening_candle(smart_api, token, exchange):
    print(f"Fetching today's opening candle for token {token} ({exchange})")
    today = datetime.now(IST).date()
    params = {
        "exchange": exchange,
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

def calculate_fib_levels(vlow, vhigh):
    """Actual precise calculation — round figures nahi, 2 decimal j."""
    rng = vhigh - vlow
    if rng <= 0:
        return []
    return [{"ratio": r, "name": n, "price": round(vlow + r * rng, 2)} for r, n in FIB_LEVELS]

def get_level(fib_levels, ratio):
    for f in fib_levels:
        if abs(f["ratio"] - ratio) < 0.001:
            return f
    return None

def analyze(candle, fib_levels, mid):
    o, h, l = candle["open"], candle["high"], candle["low"]
    tol = o * (TOLERANCE_PCT / 100)

    lvl_buy_rev = get_level(fib_levels, 0.236)     # Potential Buy Reversal
    lvl_rev_zone = get_level(fib_levels, 0.618)    # Reversal Zone
    lvl_breakout = get_level(fib_levels, 1.000)    # Potential Break out
    lvl_target1 = get_level(fib_levels, 1.272)     # Potential Target 1
    lvl_target2 = get_level(fib_levels, 1.618)     # Potential Target 2

    strength = lvl_buy_rev and l > lvl_buy_rev["price"]

    def near(price, level):
        return level and abs(price - level["price"]) <= tol

    notes = []
    prediction = []

    if strength:
        notes.append("✅ Strength (Low above Potential Buy Reversal)")
        prediction.append("Shallow pullback → uptrend continue chance high")

    if near(o, lvl_rev_zone):
        notes.append("🔄 Open at Reversal Zone (0.618)")
        prediction.append("Strong reaction zone. Possible early pause/reversal.")
    if near(o, lvl_target1):
        notes.append("⚠️ Open at Potential Target 1 (1.272)")
        prediction.append("Decision zone. Breakout = continuation, Rejection = pullback.")
    if near(o, lvl_target2):
        notes.append("🔻 Open at Potential Target 2 (1.618)")
        prediction.append("Exhaustion zone. High chance of reversal.")
    if near(o, lvl_breakout):
        notes.append("📌 Open at Potential Break out")
        prediction.append("Range extreme. Directional move expected.")

    if not notes:
        notes.append("No major Fib confluence at open")
        prediction.append("Wait for clearer reaction at key levels.")

    bias = "Bullish" if o > mid else "Bearish"

    return {
        "bias": bias,
        "notes": notes,
        "prediction": prediction,
    }

def process_index(smart_api, name, token, exchange, manual):
    print(f"\n----- Processing {name} -----")

    vlow = float(manual["val"])
    vhigh = float(manual["vah"])
    print(f"{name}: Using MANUAL levels → VLOW:{vlow} VHIGH:{vhigh} "
          f"(source={manual.get('source')}, updated={manual.get('updated')})")

    fib_levels = calculate_fib_levels(vlow, vhigh)
    mid = get_level(fib_levels, 0.5)["price"]  # Mid Zone j POC nu kaam kare che

    candle = fetch_opening_candle(smart_api, token, exchange)
    if candle is None:
        print(f"{name}: Opening candle failed")
        return None

    analysis = analyze(candle, fib_levels, mid)

    return {
        "name": name,
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "vlow": vlow,
        "vhigh": vhigh,
        "fib_levels": fib_levels,
        **analysis,
    }

def main():
    now = datetime.now(IST)
    print(f"Current time: {now}")

    manual_levels_raw = fetch_manual_levels()

    # Sirf INTRADAY type j vaparay — WEEK52 sirf website par vaparay che
    manual_levels = {
        name: data["INTRADAY"]
        for name, data in manual_levels_raw.items()
        if "INTRADAY" in data
    }

    # Fakt e instruments je na manual INTRADAY levels save thayela hoy
    to_process = [inst for inst in INSTRUMENTS if inst["name"] in manual_levels]

    if not to_process:
        print("Koi instrument nu manual INTRADAY VLOW/VHIGH nathi malyu, alert skip")
        send_telegram("ℹ️ Fib Open Alert: Aaje koi instrument nu manual VLOW/VHIGH moklyu nathi, etle alert skip thai.")
        return

    smart_api = login()

    results = []
    for inst in to_process:
        res = process_index(smart_api, inst["name"], inst["token"], inst["exchange"], manual_levels[inst["name"]])
        if res:
            results.append(res)

    if not results:
        send_telegram("❌ Fib Open Alert: No data\nCheck logs for details")
        print("No results")
        return

    lines = [f"<b>📊 Fib Open Alert</b>\n{now.strftime('%d-%b %H:%M')} IST\n"]

    for r in results:
        lines.append(f"<b>{r['name']}</b>")
        lines.append(f"O: <b>{r['open']}</b> | H: {r['high']} | L: {r['low']}")
        lines.append(f"Bias: {r['bias']}")
        lines.append("Notes: " + " | ".join(r["notes"]))
        lines.append("Prediction:")
        for p in r["prediction"]:
            lines.append(f"• {p}")
        lines.append(f"VLOW: {r['vlow']} | VHIGH: {r['vhigh']}")
        for lvl in r["fib_levels"]:
            lines.append(f"{lvl['name']}: {lvl['price']}")
        lines.append("")

    msg = "\n".join(lines)
    send_telegram(msg)
    print("Full alert sent")

if __name__ == "__main__":
    main()
