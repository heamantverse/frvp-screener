"""
Pre-market Nifty & BankNifty alert.
Gai kali ni High-Low range parthi fib levels ganine, gai kali no close
ane aaje no pre-open bhav kaya level ni najik chhe e joi ne
CALL/PUT/WATCH, SL ane Target Telegram par mokle chhe.
"""

import os
import time
import requests
import pyotp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from SmartApi import SmartConnect

API_KEY = os.environ["ANGEL_API_KEY"]
CLIENT_CODE = os.environ["ANGEL_CLIENT_ID"]
PASSWORD = os.environ["ANGEL_PASSWORD"]
TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

# Angel One index tokens — jo error aave to check karvo padse
INDEXES = [
    {"name": "NIFTY 50", "token": "99926000"},
    {"name": "NIFTY BANK", "token": "99926009"},
]

LEVELS = [
    (0.0,    "Break down",       "PUT"),
    (0.25,   "Buy Reversal",     "CALL"),
    (0.75,   "Sell Reversal",    "PUT"),
    (1.0,    "Break out",        "CALL"),
    (1.272,  "Target 1",         "WATCH"),
    (-0.272, "Target 1 (down)",  "WATCH"),
]


def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    return smart_api


def fib(ratio, lo, hi):
    return lo + (hi - lo) * ratio


def prev_day_range(smart_api, token):
    end = datetime.now()
    start = end - timedelta(days=10)
    resp = smart_api.getCandleData({
        "exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
        "fromdate": start.strftime("%Y-%m-%d 09:15"),
        "todate": end.strftime("%Y-%m-%d 15:30"),
    })
    rows = resp.get("data") or []
    if not rows:
        return None
    last = rows[-1]
    return {"date": last[0][:10], "high": float(last[2]), "low": float(last[3]), "close": float(last[4])}


def get_ltp(smart_api, token, name):
    try:
        resp = smart_api.ltpData("NSE", name, token)
        if resp.get("status") and resp.get("data"):
            return float(resp["data"]["ltp"])
    except Exception as e:
        print(f"[WARN] LTP failed for {name}: {e}")
    return None


def nearest_level(price, lo, hi):
    computed = sorted(
        [(ratio, label, action, fib(ratio, lo, hi)) for ratio, label, action in LEVELS],
        key=lambda x: x[0],
    )
    best_idx = min(range(len(computed)), key=lambda i: abs(price - computed[i][3]))
    ratio, label, action, lvl_price = computed[best_idx]
    below = computed[best_idx - 1] if best_idx > 0 else None
    above = computed[best_idx + 1] if best_idx < len(computed) - 1 else None

    if action == "CALL":
        sl = below[3] if below else lvl_price
        tp = above[3] if above else lvl_price
    elif action == "PUT":
        sl = above[3] if above else lvl_price
        tp = below[3] if below else lvl_price
    else:
        sl = tp = None

    return {
        "ratio": ratio, "label": label, "action": action, "level_price": round(lvl_price, 2),
        "sl": round(sl, 2) if sl is not None else None,
        "tp": round(tp, 2) if tp is not None else None,
    }


def fmt_block(idx_name, price, tag, sig):
    if sig is None:
        return f"{idx_name} ({tag}): data na malyu"
    emoji = {"CALL": "🟢 CALL", "PUT": "🔴 PUT", "WATCH": "🟡 WATCH"}[sig["action"]]
    lines = [f"*{idx_name}* ({tag}): {price}",
             f"Nearest: {sig['ratio']} {sig['label']} @ {sig['level_price']}",
             f"Bias: {emoji}"]
    if sig["sl"] is not None:
        lines.append(f"SL: {sig['sl']}   Target: {sig['tp']}")
    else:
        lines.append("Confirmation ni wait karo (breakout/breakdown thay tyare j entry)")
    return "\n".join(lines)


def send_telegram(text):
    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
        timeout=20,
    )
    print("Telegram status:", resp.status_code, resp.text[:200])


def main():
    smart_api = login()
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p")
    blocks = [f"*Pre-market Alert* — {now_ist} IST\n"]

    for idx in INDEXES:
        rng = prev_day_range(smart_api, idx["token"])
        if not rng:
            blocks.append(f"{idx['name']}: gai kali no data na malyo")
            continue

        lo, hi = rng["low"], rng["high"]
        close_sig = nearest_level(rng["close"], lo, hi)
        blocks.append(fmt_block(idx["name"], rng["close"], f"Gai kali close, {rng['date']}", close_sig))

        time.sleep(0.5)
        ltp = get_ltp(smart_api, idx["token"], idx["name"])
        if ltp is not None:
            today_sig = nearest_level(ltp, lo, hi)
            blocks.append(fmt_block(idx["name"], ltp, "Aaje pre-open bhav", today_sig))
        else:
            blocks.append(f"{idx['name']} (Aaje pre-open bhav): live LTP na malyo, upar no gai kali no analysis j vaapro")

        blocks.append(f"Range (gai kali): Low {lo} — High {hi}\n")

    text = "\n\n".join(blocks)
    text += "\n_Note: index ma direct trade nathi thato, options chain ma CALL/PUT levu padse. Aa filter chhe, salah nathi._"

    print(text)
    send_telegram(text)


if __name__ == "__main__":
    main()
