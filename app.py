import os
import re
import json
import time
import hmac
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, Response, jsonify

app = Flask(__name__)

GITHUB_REPO = "heamantverse/scope-data"
GITHUB_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/results.json"
CACHE_SECONDS = 600

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LATEST_FILE = os.path.join(BASE_DIR, "latest_levels.json")
IST = timezone(timedelta(hours=5, minutes=30))

_cache = {"data": None, "time": 0}

# ---- Instruments manual entry mate support kare che ----
INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"]

# ---- Level types: INTRADAY = 9:15 alert mate vaparay, WEEK52 = sirf website par ----
LEVEL_TYPES = ["INTRADAY", "WEEK52"]
DEFAULT_LEVEL_TYPE = "INTRADAY"

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


def build_message(instrument, level_type, val, vah, levels):
    label = instrument if level_type == DEFAULT_LEVEL_TYPE else f"{instrument} ({level_type})"
    lines = [
        f"<b>📐 Manual Levels — {label}</b>",
        f"VLOW: {val}  |  VHIGH: {vah}",
        "",
    ]
    for lvl in levels:
        lines.append(f"{lvl['name']}: {lvl['price']}")
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
                sent = send_telegram(build_message(instrument, level_type, val, vah, levels))
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
    send_telegram(build_message(instrument, level_type, val, vah, levels))
    return "ok"


@app.route("/fib/api/<secret>")
def fib_api(secret):
    """GitHub Actions (fib_intraday_open.py) aahi thi manual levels fetch kare che.
    Sirf INTRADAY j vaparay — WEEK52 sirf website par j vapraay che."""
    expected = os.environ.get("FIB_API_SECRET", "")
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        return jsonify({"error": "forbidden"}), 403
    return jsonify(load_latest_all())
