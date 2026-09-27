import os
import re
import json
import time
import hmac
import requests
import pyotp
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta, time as dtime
from flask import Flask, render_template, request, Response, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

GITHUB_REPO = "heamantverse/scope-data"
GITHUB_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/results.json"
CACHE_SECONDS = 600

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LATEST_FILE = os.path.join(BASE_DIR, "latest_levels.json")
LIVE_SENT_FILE = os.path.join(BASE_DIR, "live_sent.json")
IST = timezone(timedelta(hours=5, minutes=30))

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


def is_market_hours():
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def load_live_sent():
    try:
        with open(LIVE_SENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def already_sent_today(instrument):
    data = load_live_sent()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    return data.get(instrument) == today


def mark_live_sent(instrument):
    data = load_live_sent()
    data[instrument] = datetime.now(IST).strftime("%Y-%m-%d")
    tmp = LIVE_SENT_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, LIVE_SENT_FILE)
    except Exception:
        pass

_cache = {"data": None, "time": 0}

# ---- Instruments manual entry mate support kare che ----
INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"]

# Live open-candle fetch mate token/exchange (fib_intraday_open.py sathe match)
INSTRUMENT_TOKENS = {
    "NIFTY": ("99926000", "NSE"),
    "BANKNIFTY": ("99926009", "NSE"),
    "SENSEX": ("99919000", "BSE"),
}

# ---- Level types: INTRADAY = 9:15 alert + live analysis mate vaparay, WEEK52 = sirf website par ----
LEVEL_TYPES = ["INTRADAY", "WEEK52"]
DEFAULT_LEVEL_TYPE = "INTRADAY"

TOLERANCE_PCT = 0.20

# ---- Level naming (same names as the chart indicator) ----
FIB_RATIOS_NAMED = [
    (1.618, "Potential Target 2"),
    (1.272, "Potential Target 1"),
    (1.0, "Potential Break out"),
    (0.786, "Potential sell Reversal"),
    (0.618, "Reversal Zone"),
    (0.5, "Mid Zone"),
    (0.382, "Reaction Zone"),
    (0.236, "Potential Buy Reversal"),
    (0.0, "Break down"),
    (-0.272, "Potential Target 1"),
    (-1.618, "Potential Target 2"),
]


@app.before_request
def require_login():
    # Telegram webhook ane GitHub Actions no API route login mangi shakta nathi;
    # e badha ne alag secret thi j protect karel chhe
    if request.path.startswith("/telegram-webhook/") or request.path.startswith("/fib/api/"):
        return None

    user = os.environ.get("SITE_USER", "")
    pwd = os.environ.get("SITE_PASS", "")
    auth = request.authorization
    ok = (
        user and pwd and auth
        and hmac.compare_digest(auth.username or "", user)
        and hmac.compare_digest(auth.password or "", pwd)
    )
    if not ok:
        return Response(
            "Login jaruri chhe", 401,
            {"WWW-Authenticate": 'Basic realm="Login"'},
        )


def load_results():
    if _cache["data"] and time.time() - _cache["time"] < CACHE_SECONDS:
        return _cache["data"]

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN set nathi")

    resp = requests.get(
        GITHUB_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.raw+json",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    _cache["data"] = data
    _cache["time"] = time.time()
    return data


def calculate_fib_levels(val, vah):
    rng = vah - val
    if rng <= 0:
        return []
    return [
        {"ratio": ratio, "name": name, "price": round(val + ratio * rng, 2)}
        for ratio, name in FIB_RATIOS_NAMED
    ]


def get_level(levels, ratio):
    for f in levels:
        if abs(f["ratio"] - ratio) < 0.001:
            return f
    return None


# ---- Live open-candle analysis (fib_intraday_open.py jevij logic, sync/webhook mate halki version) ----

def login_angel():
    api_key = os.environ.get("ANGEL_API_KEY", "")
    client_code = os.environ.get("ANGEL_CLIENT_ID", "")
    password = os.environ.get("ANGEL_PASSWORD", "")
    totp_secret = os.environ.get("ANGEL_TOTP_SECRET", "")
    if not all([api_key, client_code, password, totp_secret]):
        print("Angel credentials missing, live analysis skip")
        return None
    try:
        smart_api = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        data = smart_api.generateSession(client_code, password, totp)
        if not data.get("status"):
            print(f"Angel login failed: {data}")
            return None
        return smart_api
    except Exception as e:
        print(f"Angel login error: {e}")
        return None


def fetch_opening_candle_quick(smart_api, token, exchange, attempts=3, wait=2):
    today = datetime.now(IST).date()
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": "THREE_MINUTE",
        "fromdate": today.strftime("%Y-%m-%d 09:15"),
        "todate": today.strftime("%Y-%m-%d 09:30"),
    }
    for attempt in range(attempts):
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
            print(f"Candle attempt {attempt+1}: {resp}")
        except Exception as e:
            print(f"Candle fetch error: {e}")
        time.sleep(wait)
    return None


def generate_trade_idea(o, levels, bias):
    """Nearby support/resistance levels parthi ek concrete idea banave che —
    kya level ni raah jovi, CE/PE levu ke nahi, ane target kya. Educational
    hint j chhe, final call/timing tamare jate levano."""
    sorted_lvls = sorted(levels, key=lambda l: l["price"])
    below = [l for l in sorted_lvls if l["price"] < o]
    above = [l for l in sorted_lvls if l["price"] > o]
    support = below[-1] if below else None
    resistance = above[0] if above else None
    target_up = above[1] if len(above) > 1 else resistance
    target_down = below[-2] if len(below) > 1 else support

    if bias == "Bullish" and support:
        target = resistance or target_up
        return (f"📈 {support['name']} (~{support['price']}) par hold/bounce ni raah jovi — "
                f"confirm thay pachi CE consider karo, target {target['name']} (~{target['price']}) "
                f"aas-pas. Confirmation vagar entry na levo.")
    elif bias == "Bearish" and resistance:
        target = support or target_down
        return (f"📉 {resistance['name']} (~{resistance['price']}) par rejection ni raah jovi — "
                f"confirm thay pachi PE consider karo, target {target['name']} (~{target['price']}) "
                f"aas-pas. Confirmation vagar entry na levo.")
    return "Clear nearby level nathi mali — abhi wait-and-watch rakho."


def analyze_open(candle, levels, mid):
    o, l = candle["open"], candle["low"]
    tol = o * (TOLERANCE_PCT / 100)

    lvl_buy_rev = get_level(levels, 0.236)
    lvl_rev_zone = get_level(levels, 0.618)
    lvl_breakout = get_level(levels, 1.000)
    lvl_target1 = get_level(levels, 1.272)
    lvl_target2 = get_level(levels, 1.618)

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
    prediction.append(generate_trade_idea(o, levels, bias))
    return {"bias": bias, "notes": notes, "prediction": prediction}


def try_live_analysis(instrument, level_type, levels):
    """INTRADAY hoy, market hours chalu hoy, aa instrument mate aaje pehla live
    analysis nathi moklyu, ane aaj ni opening candle mali jay — tyare j
    Bias/Notes/Prediction pachu ave che. Baki badhi vakhat None (fib levels j jashe)."""
    if level_type != DEFAULT_LEVEL_TYPE or instrument not in INSTRUMENT_TOKENS:
        return None
    if not is_market_hours():
        print(f"{instrument}: market hours nathi, live analysis skip")
        return None
    if already_sent_today(instrument):
        print(f"{instrument}: aaje pehla j live analysis moklai gayu chhe, skip")
        return None
    mid_lvl = get_level(levels, 0.5)
    if not mid_lvl:
        return None
    smart_api = login_angel()
    if not smart_api:
        return None
    token, exchange = INSTRUMENT_TOKENS[instrument]
    candle = fetch_opening_candle_quick(smart_api, token, exchange)
    if not candle:
        print(f"{instrument}: aaj ni opening candle nathi mali, live analysis skip")
        return None
    analysis = analyze_open(candle, levels, mid_lvl["price"])
    mark_live_sent(instrument)
    return {"candle": candle, **analysis}


def build_message(instrument, level_type, val, vah, levels, live=None):
    label = instrument if level_type == DEFAULT_LEVEL_TYPE else f"{instrument} ({level_type})"
    lines = [
        f"<b>📐 Manual Levels — {label}</b>",
        f"VLOW: {val}  |  VHIGH: {vah}",
        "",
    ]
    for lvl in levels:
        lines.append(f"{lvl['name']}: {lvl['price']}")

    if live:
        c = live["candle"]
        lines.append("")
        lines.append("<b>📊 Live Open Analysis</b>")
        lines.append(f"O: <b>{c['open']}</b> | H: {c['high']} | L: {c['low']}")
        lines.append(f"Bias: {live['bias']}")
        lines.append("Notes: " + " | ".join(live["notes"]))
        lines.append("Prediction:")
        for p in live["prediction"]:
            lines.append(f"• {p}")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


# ---- Chhella levels save / load (website + Telegram + GitHub Actions API badha same data vapre) ----
# latest_levels.json have PER-INSTRUMENT, PER-TYPE nested dict chhe:
# {"NIFTY": {"INTRADAY": {...}, "WEEK52": {...}}, "BANKNIFTY": {...}, ...}

def load_latest_all():
    try:
        with open(LATEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_latest(instrument, level_type):
    return load_latest_all().get(instrument, {}).get(level_type)


def save_latest(instrument, level_type, val, vah, levels, source):
    data = load_latest_all()
    data.setdefault(instrument, {})
    data[instrument][level_type] = {
        "val": val,
        "vah": vah,
        "levels": levels,
        "source": source,
        "updated": datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST"),
    }
    tmp = LATEST_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, LATEST_FILE)
    except Exception:
        pass


def parse_telegram_message(text):
    """Formats:
    'NIFTY 76484.86 78740.12'            → instrument, INTRADAY (default), VLOW, VHIGH
    'NIFTY WEEK52 21000 26000'           → instrument, WEEK52, VLOW, VHIGH
    """
    cleaned = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text.strip().upper())
    parts = cleaned.split()

    if len(parts) == 3:
        instrument, low_s, high_s = parts
        level_type = DEFAULT_LEVEL_TYPE
    elif len(parts) == 4:
        instrument, level_type, low_s, high_s = parts
    else:
        return None

    if instrument not in INSTRUMENTS or level_type not in LEVEL_TYPES:
        return None
    try:
        low = float(low_s)
        high = float(high_s)
    except ValueError:
        return None
    return instrument, level_type, low, high


HELP_TEXT = (
    "Instrument + [TYPE] + VLOW + VHIGH space thi alag karine moklo.\n"
    "Udaharan (Intraday, default): NIFTY 76484.86 78740.12\n"
    "Udaharan (52-week): NIFTY WEEK52 21000 26000\n"
    f"Instruments: {', '.join(INSTRUMENTS)}\n"
    f"Types: {', '.join(LEVEL_TYPES)} (type na aapo to INTRADAY default)"
)


@app.route("/")
def index():
    try:
        data = load_results()
    except Exception as e:
        return f"Data load na thayo: {e}"

    return render_template(
        "index.html",
        stocks=data["stocks"],
        last_updated=data["last_updated"],
        market=data.get("market"),
    )


@app.route("/fib", methods=["GET", "POST"])
def fib_levels():
    levels = None
    error = None
    val = vah = None
    sent = None          # None = Telegram status batavvu nathi (sirf GET)
    updated = None
    source = None
    instrument = request.values.get("instrument", "NIFTY").upper()
    if instrument not in INSTRUMENTS:
        instrument = "NIFTY"
    level_type = request.values.get("type", DEFAULT_LEVEL_TYPE).upper()
    if level_type not in LEVEL_TYPES:
        level_type = DEFAULT_LEVEL_TYPE

    if request.method == "POST":
        try:
            val = float(request.form.get("val", "").strip())
            vah = float(request.form.get("vah", "").strip())
            if vah <= val:
                error = "VHIGH, VLOW thi vadhare hovu joiye"
            else:
                levels = calculate_fib_levels(val, vah)
                save_latest(instrument, level_type, val, vah, levels, "Website")
                live = try_live_analysis(instrument, level_type, levels)
                sent = send_telegram(build_message(instrument, level_type, val, vah, levels, live))
                latest = load_latest(instrument, level_type)
                if latest:
                    updated = latest["updated"]
                    source = latest["source"]
        except (TypeError, ValueError):
            error = "Sachi numeric value nakho"
    else:
        latest = load_latest(instrument, level_type)
        if latest:
            val = latest["val"]
            vah = latest["vah"]
            levels = latest["levels"]
            updated = latest["updated"]
            source = latest["source"]

    return render_template(
        "fib.html", levels=levels, error=error, val=val, vah=vah,
        sent=sent, updated=updated, source=source,
        instrument=instrument, instruments=INSTRUMENTS,
        level_type=level_type, level_types=LEVEL_TYPES,
    )


@app.route("/telegram-webhook/<secret>", methods=["POST"])
def telegram_webhook(secret):
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        return "forbidden", 403

    update = request.get_json(silent=True) or {}
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    allowed = str(os.environ.get("TELEGRAM_CHAT_ID", ""))

    # Fakt tamara chat na message swikaro, bija na nahi
    if not msg or not allowed or chat_id != allowed:
        return "ok"

    text = (msg.get("text") or "").strip()
    if not text or text.startswith("/"):
        send_telegram(HELP_TEXT)
        return "ok"

    parsed = parse_telegram_message(text)
    if not parsed:
        send_telegram("Sachi format nathi mali.\n" + HELP_TEXT)
        return "ok"

    instrument, level_type, val, vah = parsed
    if vah <= val:
        send_telegram("VHIGH, VLOW thi vadhare hovu joiye.\n" + HELP_TEXT)
        return "ok"

    levels = calculate_fib_levels(val, vah)
    save_latest(instrument, level_type, val, vah, levels, "Telegram")
    live = try_live_analysis(instrument, level_type, levels)
    send_telegram(build_message(instrument, level_type, val, vah, levels, live))
    return "ok"


@app.route("/fib/api/<secret>")
def fib_api(secret):
    """GitHub Actions (fib_intraday_open.py) aahi thi manual levels fetch kare che.
    Sirf INTRADAY j vaparay — WEEK52 sirf website par j vapraay che."""
    expected = os.environ.get("FIB_API_SECRET", "")
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        return jsonify({"error": "forbidden"}), 403
    return jsonify(load_latest_all())


# ============================================================
# STOCK SEARCH — koi pan stock (RELIANCE, TCS...) search karva mate.
# WEEK52 = FRVP (volume profile + Fib), screener_job.py jevij logic.
# INTRADAY = gai kal na high-low thi Fib + aaj ni opening candle live analysis.
# ============================================================

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
_instrument_cache = {"map": None, "time": 0}
INSTRUMENT_CACHE_SECONDS = 6 * 3600  # 6 kalak cache (aakhi master list moti chhe)

WEEK52_INTERVAL = "THIRTY_MINUTE"
WEEK52_WEEKS_LOOKBACK = 52
WEEK52_CHUNK_DAYS = 28
WEEK52_VALUE_AREA_PCT = 0.70
WEEK52_NUM_BINS = 24


def load_symbol_token_map():
    """NSE equity na badha symbols -> Angel token, 6 kalak cache thi (moti file, roj-roj na mangavi)."""
    if _instrument_cache["map"] and time.time() - _instrument_cache["time"] < INSTRUMENT_CACHE_SECONDS:
        return _instrument_cache["map"]
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    master = resp.json()
    mapping = {}
    for row in master:
        if row.get("exch_seg") == "NSE" and str(row.get("symbol", "")).endswith("-EQ"):
            mapping[row["symbol"][:-3]] = str(row["token"])
    _instrument_cache["map"] = mapping
    _instrument_cache["time"] = time.time()
    return mapping


def find_in_results(symbol):
    """Aaj no screener already aa stock ni 52-week detail calculate kari chuko hoy to e j vaparo (fast)."""
    try:
        data = load_results()
    except Exception:
        return None
    for s in data.get("stocks", []):
        if s.get("symbol") == symbol:
            return s
    return None


def fetch_52_week_data(smart_api, token):
    end = datetime.now()
    start = end - timedelta(weeks=WEEK52_WEEKS_LOOKBACK)
    all_chunks = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=WEEK52_CHUNK_DAYS), end)
        params = {
            "exchange": "NSE", "symboltoken": token, "interval": WEEK52_INTERVAL,
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
        time.sleep(0.4)

    if not all_chunks:
        return pd.DataFrame()
    df = pd.DataFrame(all_chunks, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def calculate_volume_profile(df, num_bins=WEEK52_NUM_BINS, value_area_pct=WEEK52_VALUE_AREA_PCT):
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
    }


def get_previous_trading_day():
    now = datetime.now(IST)
    prev = now.date() - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def fetch_previous_day_range(smart_api, token, exchange="NSE"):
    prev_day = get_previous_trading_day()
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": "FIVE_MINUTE",
        "fromdate": prev_day.strftime("%Y-%m-%d 09:15"),
        "todate": prev_day.strftime("%Y-%m-%d 15:30"),
    }
    try:
        resp = smart_api.getCandleData(params)
        if resp.get("status") and resp.get("data"):
            rows = resp["data"]
            highs = [float(r[2]) for r in rows]
            lows = [float(r[3]) for r in rows]
            if highs and lows:
                return {"VLOW": round(min(lows), 2), "VHIGH": round(max(highs), 2)}
    except Exception as e:
        print(f"Previous day range fetch error: {e}")
    return None


@app.route("/stock", methods=["GET"])
def stock_search():
    symbol = request.args.get("symbol", "").strip().upper()
    level_type = request.args.get("type", "WEEK52").upper()
    if level_type not in ("WEEK52", "INTRADAY"):
        level_type = "WEEK52"

    result = None
    error = None
    note = None

    if symbol:
        token_map = load_symbol_token_map()
        token = token_map.get(symbol)

        if not token:
            error = f"'{symbol}' NSE ma nathi malyu — symbol sacho lakho (dat. RELIANCE, TCS)"
        elif level_type == "WEEK52":
            cached = find_in_results(symbol)
            if cached:
                val, vah, ltp = cached["val"], cached["vah"], cached.get("ltp")
                note = "Aaje na screener run mathi (already calculate thayelu)"
            else:
                smart_api = login_angel()
                if not smart_api:
                    error = "Angel One login fail thayu (credentials PythonAnywhere par set chhe ke check karo)"
                else:
                    note = "Aa symbol roj na screener list ma nathi, etle live calculate karyu (52-week data, thodi vaar lagi)"
                    df = fetch_52_week_data(smart_api, token)
                    vp = calculate_volume_profile(df)
                    if vp is None:
                        error = f"'{symbol}' nu 52-week data na malyu"
                    else:
                        val, vah, ltp = vp["VAL"], vp["VAH"], vp["LTP"]
            if not error:
                levels = calculate_fib_levels(val, vah)
                result = {"symbol": symbol, "val": val, "vah": vah, "ltp": ltp, "levels": levels}
        else:  # INTRADAY
            smart_api = login_angel()
            if not smart_api:
                error = "Angel One login fail thayu (credentials PythonAnywhere par set chhe ke check karo)"
            else:
                rng = fetch_previous_day_range(smart_api, token)
                if not rng:
                    error = f"'{symbol}' nu gai kal nu data na malyu"
                else:
                    val, vah = rng["VLOW"], rng["VHIGH"]
                    levels = calculate_fib_levels(val, vah)
                    mid_lvl = get_level(levels, 0.5)
                    live = None
                    candle = fetch_opening_candle_quick(smart_api, token, "NSE")
                    if candle and mid_lvl:
                        live = {"candle": candle, **analyze_open(candle, levels, mid_lvl["price"])}
                        note = "Gai kal na high-low + aaj ni opening candle thi (live)"
                    else:
                        note = "Gai kal na high-low thi (aaj ni opening candle nathi mali)"
                    result = {"symbol": symbol, "val": val, "vah": vah, "ltp": None, "levels": levels, "live": live}

    return render_template(
        "stock.html", symbol=symbol, level_type=level_type,
        result=result, error=error, note=note,
    )
