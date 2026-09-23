```python
import os
import json
import time
import requests
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

PDF_FILE = "Fib_Open_Alert.pdf"
RESULT_FILE = "results.json"

# Telegram messages with exactly 4 numbers are accepted.
# Order:
# 1 = VAH
# 2 = POC
# 3 = VAL
# 4 = OPEN


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

TOLERANCE_PCT = 0.20


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def get_updates(offset=None):
    params = {
        "timeout": 10,
    }

    if offset is not None:
        params["offset"] = offset

    r = requests.get(
        telegram_url("getUpdates"),
        params=params,
        timeout=20
    )

    r.raise_for_status()
    return r.json()


def send_message(text):
    requests.post(
        telegram_url("sendMessage"),
        json={
            "chat_id": CHAT_ID,
            "text": text,
        },
        timeout=20,
    )


def send_pdf():
    with open(PDF_FILE, "rb") as f:
        requests.post(
            telegram_url("sendDocument"),
            data={
                "chat_id": CHAT_ID,
                "caption": "📊 Fib Open Alert PDF",
            },
            files={
                "document": (
                    PDF_FILE,
                    f,
                    "application/pdf"
                )
            },
            timeout=30,
        )


# ============================================================
# INPUT PARSER
# ============================================================

def parse_four_points(text):
    """
    Expected:

    56436.65
    56260
    56130.5
    56229.05

    Order:
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
        values = [float(x) for x in lines]
    except ValueError:
        return None

    vah, poc, val, opening = values

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


# ============================================================
# FIB CALCULATION
# ============================================================

def calculate_fib_levels(val, vah):
    rng = vah - val

    if rng <= 0:
        return []

    result = []

    for ratio, name in FIB_LEVELS:
        price = round(val + ratio * rng, 2)

        result.append({
            "ratio": ratio,
            "name": name,
            "price": price,
        })

    return result


def get_level(fib_levels, ratio):
    for level in fib_levels:
        if abs(level["ratio"] - ratio) < 0.001:
            return level

    return None


# ============================================================
# ANALYSIS
# ============================================================

def analyze(opening, fib_levels, poc):
    tol = opening * (TOLERANCE_PCT / 100)

    lvl_0236 = get_level(fib_levels, 0.236)
    lvl_0618 = get_level(fib_levels, 0.618)
    lvl_1000 = get_level(fib_levels, 1.000)
    lvl_1272 = get_level(fib_levels, 1.272)
    lvl_1618 = get_level(fib_levels, 1.618)

    notes = []
    predictions = []

    # Opening relative to POC
    if opening > poc:
        bias = "Bullish"
    elif opening < poc:
        bias = "Bearish"
    else:
        bias = "Neutral"

    # Strength above 0.236
    if lvl_0236 and opening > lvl_0236["price"]:
        notes.append("Strength: Open above 0.236")
        predictions.append(
            "Price is holding above the 0.236 Fib level."
        )

    # Golden reversal
    if lvl_0618 and abs(opening - lvl_0618["price"]) <= tol:
        notes.append("Open near 0.618 Golden Reversal")
        predictions.append(
            "Possible reaction / pause near the 0.618 level."
        )

    # 1.272
    if lvl_1272 and abs(opening - lvl_1272["price"]) <= tol:
        notes.append("Open near 1.272 Stall Zone")
```
