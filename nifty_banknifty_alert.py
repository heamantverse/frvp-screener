"""
Pre-market Nifty & BankNifty options table.
Har index mate CE-ATM, CE-ITM, PE-ATM, PE-ITM — chaare options no potano
data (POC/VAH/VAL, named levels, SL/Target) ane Greeks (delta/theta).
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

API_KEY = os.environ["ANGEL_API_KEY"]
CLIENT_CODE = os.environ["ANGEL_CLIENT_ID"]
PASSWORD = os.environ["ANGEL_PASSWORD"]
TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"]

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

INDEXES = [
    {"name": "NIFTY", "opt_name": "NIFTY", "token": "99926000", "step": 50},
    {"name": "BANKNIFTY", "opt_name": "BANKNIFTY", "token": "99926009", "step": 100},
]

BIAS_LEVELS = [
    (0.0,   "Base Move (bottom)", "PUT"),
    (0.25,  "Buy Reversal",       "CALL"),
    (0.75,  "Sell Reversal",      "PUT"),
    (1.0,   "Base Move (top)",    "CALL"),
    (1.272, "Stall Zone",         "WATCH"),
]

FIB_RATIOS_NAMED = [
    (1.618, "Golden Reversal"),
    (1.272, "Stall Zone"),
    (1.0,   "Base Move"),
    (0.786, "Deep Zone"),
    (0.618, "Reversal Zone"),
    (0.5,   "Mid Zone"),
    (0.382, "Reaction Zone"),
    (0.236, "Early Reversal"),
    (0.0,   "Base Move"),
    (-0.272, "Day Low Zone"),
    (-1.618, "Extreme Zone"),
]

OUT_FILE = "index_alert.json"


def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
