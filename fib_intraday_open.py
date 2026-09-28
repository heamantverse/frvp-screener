"""
Intraday Fib Open Alert – Nifty + BankNifty + Sensex
Fakt e instrument process thay je nu manual VLOW/VHIGH Telegram par
moklyu hoy (website/Telegram → PythonAnywhere). Jena manual levels
nathi e instrument skip thai jay — auto previous-day fallback nathi.

Logic (pehli 3-min candle na O/H/L/C par):
  1. Candle Breakdown/Break up sathe kevi vartay (accepted / fake) e classify thay
  2. Candle no size Fib range sathe compare thay (expansion hoy to chase na karvu)
  3. Entry / SL / aagal na 2 targets / R:R plan banave
Message ma fakt 6 levels dekhay (naam + price, ratio nahi):
  Golden Reversal T1, Break up, Golden Reversal, Breakdown, Day Low, Bounce Back
"""

import os
import time
import requests
import pyotp
from datetime import datetime
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

INTERVAL = "THREE_MINUTE"

# Level touch/near tolerance = Fib range no 10% (ochhama ochhu open na 0.01%)
NEAR_PCT_OF_RANGE = 0.10
NEAR_MIN_PCT_OF_PRICE = 0.0001
# Candle range, Fib range thi aatla gana vadhu hoy to "expansion candle"
EXPANSION_MULT = 1.5
# R:R aa thi ochhu hoy to skip/wait no warning
MIN_RR = 1.5

# Badha levels internally calculate thay (analysis mate); message ma fakt DISPLAY_RATIOS dekhay.
# 0.25 / 0.75 chart indicator sathe match che.
FIB_LEVELS = [
    (1.618, "Golden Reversal T1"),
    (1.272, "Potential Target 1"),
    (1.000, "Break up"),
    (0.750, "Potential sell Reversal"),
    (0.618, "Golden Reversal"),
    (0.500, "Mid Zone"),
    (0.382, "Reaction Zone"),
    (0.250, "Potential Buy Reversal"),
    (0.000, "Breakdown"),
    (-0.272, "Day Low"),
    (-0.618, "Bounce Back"),
    (-1.618, "Potential Target 2"),
]

# Message ma je levels batavvana (upar thi niche)
DISPLAY_RATIOS = [1.618, 1.0, 0.618, 0.0, -0.272, -0.618]

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


def nearest_level_within(fib_levels, price, tol):
    best = None
    for f in fib_levels:
        d = abs(f["price"] - price)
        if d <= tol and (best is None or d < best[0]):
            best = (d, f)
    return best[1] if best else None


def levels_beyond(fib_levels, price, tol, above=True, n=2):
    """price thi upar (above=True) ke niche (above=False) na nazdik na n levels, nazdik thi door."""
    lv = sorted(fib_levels, key=lambda x: x["price"])
    if above:
        return [x for x in lv if x["price"] > price + tol][:n]
    below = [x for x in lv if x["price"] < price - tol]
    return list(reversed(below))[:n]


def adjacent_level(fib_levels, price, tol, above=True):
    r = levels_beyond(fib_levels, price, tol, above, 1)
    return r[0] if r else None


def build_plan(direction, setup, candle, fib_levels, tol, expansion, L0, L1):
    """Entry / SL / aagal na 2 targets / R:R. Fakt PE ke CE direction mate."""
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    is_pe = direction == "PE"
    accepted = setup in ("Breakdown accepted", "Breakout accepted")

    if accepted and expansion:
        # Candle bahu moti — chase nahi, broken level na retest ni raah
        broken = L0 if is_pe else L1
        entry = broken["price"]
        entry_txt = f"{broken['name']} ({entry}) par pullback/retest ma {'sell' if is_pe else 'buy'}"
        adj = adjacent_level(fib_levels, entry, tol, above=is_pe)
        sl = adj["price"] if adj else (h if is_pe else l)
        sl_txt = adj["name"] if adj else ("candle high" if is_pe else "candle low")
    elif accepted:
        broken = L0 if is_pe else L1
        entry = l if is_pe else h
        entry_txt = f"candle {'low' if is_pe else 'high'} ({entry}) todi ne"
        sl = broken["price"]
        sl_txt = broken["name"]
    else:
        entry = l if is_pe else h
        entry_txt = f"candle {'low' if is_pe else 'high'} ({entry}) todi ne"
        sl = h if is_pe else l
        sl_txt = "candle high" if is_pe else "candle low"

    targets = levels_beyond(fib_levels, entry, tol, above=not is_pe, n=2)
    risk = abs(entry - sl)

    parts = [f"Entry {entry_txt}", f"SL {round(sl, 2)} ({sl_txt})"]
    if targets:
        parts.append("Target " + " → ".join(f"{t['name']} ({t['price']})" for t in targets))
    else:
        parts.append("Target: aagal koi level nathi, SL trail karo")

    rr_list = []
    if risk > 0.0001:
        for t in targets:
            rr_list.append(round(abs(t["price"] - entry) / risk, 1))
    if rr_list:
        parts.append("R:R " + " / ".join(str(x) for x in rr_list))
    line = f"{'📉 PE' if is_pe else '📈 CE'} plan: " + " | ".join(parts)
    if rr_list and rr_list[-1] < MIN_RR:
        line += " ⚠️ R:R kam — skip/wait"
    if accepted and expansion:
        line += " (retest na aave to trade nahi)"
    return line


def analyze(candle, fib_levels, vlow, vhigh):
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    fib_range = vhigh - vlow
    tol = max(NEAR_PCT_OF_RANGE * fib_range, o * NEAR_MIN_PCT_OF_PRICE)

    L0 = get_level(fib_levels, 0.0)        # Breakdown
    L1 = get_level(fib_levels, 1.0)        # Break up
    mid = get_level(fib_levels, 0.5)["price"]
    L0p, L1p = L0["price"], L1["price"]

    rng = max(h - l, 0.01)
    body_pct = abs(c - o) / rng
    upper_wick = (h - max(o, c)) / rng
    lower_wick = (min(o, c) - l) / rng
    expansion = rng > EXPANSION_MULT * fib_range

    notes = []
    prediction = []

    # ---- Open kya chhe (range-based tolerance) ----
    def near(price, level):
        return level and abs(price - level["price"]) <= tol

    if o > L1p + tol:
        notes.append(f"⬆️ Gap up open ({L1['name']} {L1p} ni upar)")
    elif o < L0p - tol:
        notes.append(f"⬇️ Gap down open ({L0['name']} {L0p} ni niche)")

    lvl_buy_rev = get_level(fib_levels, 0.25)
    if lvl_buy_rev and l > lvl_buy_rev["price"]:
        notes.append("✅ Strength (Low above Potential Buy Reversal)")
        prediction.append("Shallow pullback → uptrend continue chance high")

    lvl_rev_zone = get_level(fib_levels, 0.618)
    lvl_target1 = get_level(fib_levels, 1.272)
    lvl_target2 = get_level(fib_levels, 1.618)
    if near(o, L0):
        notes.append(f"🎯 Open at Breakdown ({L0p})")
        prediction.append("Range bottom. Close niche = downside continuation, reclaim = bounce. Candle close par nirbhar.")
    if near(o, lvl_rev_zone):
        notes.append(f"🔄 Open at Golden Reversal ({lvl_rev_zone['price']})")
        prediction.append("Strong reaction zone. Possible early pause/reversal.")
    if near(o, lvl_target1):
        notes.append(f"⚠️ Open at Potential Target 1 ({lvl_target1['price']})")
        prediction.append("Decision zone. Breakout = continuation, Rejection = pullback.")
    if near(o, lvl_target2):
        notes.append(f"🔻 Open at Golden Reversal T1 ({lvl_target2['price']})")
        prediction.append("Exhaustion zone. High chance of reversal.")
    if near(o, L1):
        notes.append(f"📌 Open at Break up ({L1p})")
        prediction.append("Range extreme. Directional move expected.")
    if not notes:
        notes.append("No major Fib confluence at open")

    # ---- Candle setup classify ----
    setup = None
    direction = "WAIT"
    reason = ""

    if c < L0p - tol and l < L0p:
        setup, direction = "Breakdown accepted", "PE"
        setup_txt = f"Breakdown accepted — candle Breakdown ({L0p}) ni niche close thayi"
    elif l < L0p - tol and c >= L0p:
        setup, direction = "Fake breakdown", "CE"
        setup_txt = f"Fake breakdown — Breakdown ni niche gayo pan pachu upar close thayo"
    elif c > L1p + tol and h > L1p:
        setup, direction = "Breakout accepted", "CE"
        setup_txt = f"Breakout accepted — candle Break up ({L1p}) ni upar close thayi"
    elif h > L1p + tol and c <= L1p:
        setup, direction = "Fake breakout", "PE"
        setup_txt = f"Fake breakout — Break up ni upar gayo pan pachu niche close thayo"
    else:
        rej = None
        if lower_wick >= 0.5:
            lv = nearest_level_within(fib_levels, l, tol)
            if lv:
                rej = ("CE", lv, "support")
        if not rej and upper_wick >= 0.5:
            lv = nearest_level_within(fib_levels, h, tol)
            if lv:
                rej = ("PE", lv, "resistance")
        if rej:
            direction = rej[0]
            setup = "Level rejection"
            setup_txt = f"{rej[1]['name']} ({rej[1]['price']}) par rejection ({rej[2]})"
        elif body_pct >= 0.5 and rng >= 0.35 * fib_range and ((c > o and c > mid) or (c < o and c < mid)):
            direction = "CE" if c > o else "PE"
            setup = "Directional candle"
            setup_txt = "Range ni andar directional candle"
        else:
            setup = "Indecision"
            setup_txt = "Indecision candle — clear direction nathi"
            reason = "nani body / range ni vachche"

    if direction in ("PE", "CE"):
        if expansion:
            prediction.append(
                f"⚡ Pehli candle bahu moti (range {round(rng)} vs Fib range {round(fib_range)}) — "
                f"chase na karo, Fib extension levels par bharoso ochho.")
        prediction.append(build_plan(direction, setup, candle, fib_levels, tol, expansion, L0, L1))
    else:
        prediction.append(
            f"⏸️ Clear setup nathi ({reason}) — candle high ({h}) ke low ({l}) no break jova sudhi wait.")

    bias = {"PE": "Bearish", "CE": "Bullish"}.get(direction, "Neutral")
    return {"bias": bias, "setup": setup_txt, "notes": notes, "prediction": prediction}


def process_index(smart_api, name, token, exchange, manual):
    print(f"\n----- Processing {name} -----")

    vlow = float(manual["val"])
    vhigh = float(manual["vah"])
    print(f"{name}: Using MANUAL levels → VLOW:{vlow} VHIGH:{vhigh} "
          f"(source={manual.get('source')}, updated={manual.get('updated')})")

    fib_levels = calculate_fib_levels(vlow, vhigh)
    if not fib_levels:
        print(f"{name}: VHIGH <= VLOW, skip")
        return None

    candle = fetch_opening_candle(smart_api, token, exchange)
    if candle is None:
        print(f"{name}: Opening candle failed")
        return None

    analysis = analyze(candle, fib_levels, vlow, vhigh)

    return {
        "name": name,
        "candle": candle,
        "vlow": vlow,
        "vhigh": vhigh,
        "fib_levels": fib_levels,
        **analysis,
    }


def build_message(now, results):
    lines = [f"<b>📊 Fib Open Alert</b>\n{now.strftime('%d-%b %H:%M')} IST\n"]
    for r in results:
        c = r["candle"]
        lines.append(f"<b>{r['name']}</b>")
        lines.append(f"O: <b>{c['open']}</b> | H: {c['high']} | L: {c['low']} | C: {c['close']}")
        lines.append(f"Setup: {r['setup']}")
        lines.append(f"Bias: {r['bias']}")
        lines.append("Notes: " + " | ".join(r["notes"]))
        lines.append("Prediction:")
        for p in r["prediction"]:
            lines.append(f"• {p}")
        lines.append(f"VLOW: {r['vlow']} | VHIGH: {r['vhigh']}")
        # Fakt tamara mangya te 6 levels (upar thi niche), ratio vagar
        for ratio in DISPLAY_RATIOS:
            lvl = get_level(r["fib_levels"], ratio)
            if lvl:
                lines.append(f"{lvl['name']}: {lvl['price']}")
        lines.append("")
    lines.append("Note: Aa scenario/filter chhe, kharidva-vechvani salah nathi.")
    return "\n".join(lines)


def main():
    now = datetime.now(IST)
    print(f"Current time: {now}")

    manual_levels_raw = fetch_manual_levels()

    # Sirf INTRADAY type j vaparay — WEEK52 sirf website par vaparay che
    manual_levels = {
        name: data["INTRADAY"]
        for name, data in manual_levels_raw.items()
        if isinstance(data, dict) and "INTRADAY" in data
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

    send_telegram(build_message(now, results))
    print("Full alert sent")


if __name__ == "__main__":
    main()
