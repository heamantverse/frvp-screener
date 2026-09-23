"""
Temporary debug version – Fib Intraday Open Alert
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

print("=" * 50)
print("SCRIPT STARTED")
print("=" * 50)

API_KEY = os.environ.get("ANGEL_API_KEY", "")
CLIENT_CODE = os.environ.get("ANGEL_CLIENT_ID", "")
PASSWORD = os.environ.get("ANGEL_PASSWORD", "")
TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

print(f"API_KEY present: {bool(API_KEY)}")
print(f"CLIENT_CODE present: {bool(CLIENT_CODE)}")
print(f"PASSWORD present: {bool(PASSWORD)}")
print(f"TOTP_SECRET present: {bool(TOTP_SECRET)}")
print(f"TELEGRAM_BOT_TOKEN present: {bool(TELEGRAM_BOT_TOKEN)}")
print(f"TELEGRAM_CHAT_ID present: {bool(TELEGRAM_CHAT_ID)}")

NIFTY_TOKEN = "99926000"
BANKNIFTY_TOKEN = "99926009"
INTERVAL = "FIVE_MINUTE"
IST = ZoneInfo("Asia/Kolkata")

def login():
    print("Trying to login...")
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    print(f"Generated TOTP: {totp}")
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    print(f"Login response status: {data.get('status')}")
    if not data.get("status"):
        print(f"Login failed: {data}")
        raise SystemExit("Login failed")
    print("Login successful!")
    return smart_api

def send_telegram(message: str):
    print("Sending Telegram message...")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram response: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

def main():
    now = datetime.now(IST)
    print(f"Current time: {now}")

    try:
        smart_api = login()
    except Exception as e:
        print(f"Login exception: {e}")
        send_telegram(f"❌ Login failed: {e}")
        return

    send_telegram("✅ Debug: Script ran successfully + Login OK")
    print("Debug message sent to Telegram")

if __name__ == "__main__":
    main()
