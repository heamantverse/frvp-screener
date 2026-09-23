"""
Flask Website - PythonAnywhere

Existing:
    / 
    -> results.json from private GitHub repo

New:
    /telegram-webhook
    -> receives 4 Fib values from Telegram
    -> triggers GitHub Actions
"""

import os
import time
import requests

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
)


app = Flask(__name__)


# ============================================================
# EXISTING WEBSITE CONFIG
# ============================================================

GITHUB_REPO = "heamantverse/frvp-screener"

GITHUB_URL = (
    f"https://api.github.com/repos/"
    f"{GITHUB_REPO}/contents/results.json"
)

CACHE_SECONDS = 600

_cache = {
    "data": None,
    "time": 0,
}


# ============================================================
# GITHUB
# ============================================================

def github_headers():
    token = os.environ.get("GITHUB_TOKEN")

    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN set nathi"
        )

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_results():
    if (
        _cache["data"]
        and time.time() - _cache["time"] < CACHE_SECONDS
    ):
        return _cache["data"]

    response = requests.get(
        GITHUB_URL,
        headers=github_headers(),
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    _cache["data"] = data
    _cache["time"] = time.time()

    return data


# ============================================================
# EXISTING WEBSITE
# ============================================================

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
    )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

def parse_four_points(text):
    """
    Expected:

    VAH
    POC
    VAL
    OPEN
    """

    lines = [
        x.strip()
        for x in text.splitlines()
        if x.strip()
    ]

    if len(lines) != 4:
        return None

    try:
        values = [
            float(x)
            for x in lines
        ]

    except ValueError:
        return None

    vah, poc, val, opening = values

    # Validation
    if vah <= val:
        return None

    if poc < val or poc > vah:
        return None

    return {
        "vah": vah,
        "poc": poc,
        "val": val,
        "opening": opening,
    }


def trigger_github_action(points):

    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_REPO}/dispatches"
    )

    payload = {
        "event_type": "fib_input",
        "client_payload": {
            "vah": points["vah"],
            "poc": points["poc"],
            "val": points["val"],
            "opening": points["opening"],
        },
    }

    response = requests.post(
        url,
        headers=github_headers(),
        json=payload,
        timeout=20,
    )

    response.raise_for_status()


def telegram_send_message(chat_id, text):

    # Telegram BOT TOKEN PythonAnywhere પર રાખવાની જરૂર નથી.
    # Error response માત્ર webhook caller માટે છે.
    #
    # GitHub Action actual Telegram response મોકલશે.
    #
    # એટલે અહીં acknowledgment મોકલવા માટે optional
    # TELEGRAM_WEBHOOK_TOKEN વાપરી શકાય.
    #
    # હાલ simple workflow રાખ્યો છે.

    return


@app.route(
    "/telegram-webhook",
    methods=["POST"]
)
def telegram_webhook():

    try:

        update = request.get_json(
            silent=True
        )

        if not update:
            return jsonify({
                "ok": False,
                "error": "Invalid JSON",
            }), 400

        message = update.get("message")

        if not message:
            return jsonify({
                "ok": True,
            })

        chat = message.get("chat", {})

        chat_id = str(
            chat.get("id", "")
        )

        allowed_chat_id = str(
            os.environ.get(
                "TELEGRAM_CHAT_ID",
                "",
            )
        )

        # Security:
        # માત્ર તમારું Telegram chat accepted.
        if (
            not allowed_chat_id
            or chat_id != allowed_chat_id
        ):
            return jsonify({
                "ok": False,
                "error": "Unauthorized chat",
            }), 403

        text = message.get(
            "text",
            "",
        ).strip()

        points = parse_four_points(
            text
        )

        if not points:

            return jsonify({
                "ok": False,
                "error": (
                    "Send exactly 4 numbers:\n"
                    "VAH\n"
                    "POC\n"
                    "VAL\n"
                    "OPEN"
                ),
            }), 400

        # Trigger GitHub Actions
        trigger_github_action(
            points
        )

        print(
            "Fib GitHub Action triggered:",
            points,
        )

        return jsonify({
            "ok": True,
            "message": "Fib analysis started",
        }), 200

    except Exception as e:

        print(
            "Telegram webhook error:",
            e,
        )

        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
    })


# ============================================================
# LOCAL
# ============================================================

if __name__ == "__main__":
    app.run(
        debug=True
    )
