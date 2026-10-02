"""
Pre-market Nifty & BankNifty options alert (Telegram input version).

Flow:
  1. Login + Greeks (delta/theta) Angel One thi pehla thi j fetch kari lo (wait ni darmiyan).
  2. Website API thi aajno Telegram input vancho (INPUT_DEADLINE_IST = 09:10 sudhi raah jovo,
     badhu input aavi jay to vahelu aagal vadho).
  3. Index + 4 options (CE ATM/ITM, PE ATM/ITM) mate Best Entry, Target, SL kadho.
  4. index_alert.json lakho -> make_index_alert_pdf.py PDF banave.

Telegram message format (ek j message ma pan chale, alag alag pan chale):

    NIFTY VAL 22522.15 VAH 22590.05   (athva 9:18 alert vala INTRADAY levels)
    BANKNIFTY VAL 54555.1 VAH 54905.05
    NIFTY PRICE 22525.65      <- pre-open price (9:10 sudhi ma)
    BANKNIFTY PRICE 54690.95
    22550CE VAL 120 VAH 160
    22500CE VAL 140 VAH 180
    22550PE VAL 110 VAH 150
    22600PE VAL 150 VAH 190
    54700CE VAL 980 VAH 1100     <- BANKNIFTY options pan em j
    (optional: "NIFTY 22550CE LOW .. HIGH .." athva ant ma "LTP 131" lakhi shako)
"""

import os
import re
import time
import json
import pandas as pd
import requests
import pyotp
from datetime import datetime
from zoneinfo import ZoneInfo
from SmartApi import SmartConnect
from SmartApi.smartExceptions import DataException

IST = ZoneInfo("Asia/Kolkata")

API_KEY = os.environ["ANGEL_API_KEY"]
CLIENT_CODE = os.environ["ANGEL_CLIENT_ID"]
PASSWORD = os.environ["ANGEL_PASSWORD"]
TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"]
WEBSITE_URL = os.environ["WEBSITE_URL"].rstrip("/")
FIB_API_SECRET = os.environ["FIB_API_SECRET"]
INPUT_DEADLINE_IST = os.environ.get("INPUT_DEADLINE_IST", "09:10")

INSTRUMENT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

INDEXES = [
    {"name": "NIFTY", "opt_name": "NIFTY", "token": "99926000"},
    {"name": "BANKNIFTY", "opt_name": "BANKNIFTY", "token": "99926009"},
]

# ============================================================
# CONFIG â€” Best Entry / Target / SL na badha points aa j jagya e badlo
# ============================================================
ENTRY_RATIO = -0.272        # Best Entry = VAL - 0.272 x Range (index ane option banne mate)
TARGET_EXT = 0.272          # Index target = VAH + 0.272 x Range (CALL) / VAL - 0.272 x Range (PUT)
SL_RANGE_PCT = 0.30         # Index SL = Price -/+ 30% of Range
HOLD_DAY_FRACTION = 0.5     # Theta decay ketla divas no ganvo (~3 kalak = 0.5 divas)
MIN_RR = 1.5                # Aa thi ochho Risk:Reward hoy to "SKIP"
OPTIONS_PER_INDEX = 4       # CE ATM, CE ITM, PE ATM, PE ITM

# Bias (index price kya level ni nazdik chhe) â€” alert ma faqat CALL/PUT/WATCH dekhase
BIAS_LEVELS = [
    (0.0,   "PUT"),
    (0.25,  "CALL"),
    (0.75,  "PUT"),
    (1.0,   "CALL"),
    (1.272, "WATCH"),
]

OUT_FILE = "index_alert.json"


def fib(ratio, lo, hi):
    return lo + (hi - lo) * ratio


# ============================================================
# RATE-LIMIT SAFE WRAPPER
# ============================================================

def safe_call(fn, *args, retries=8, base_delay=8, max_wait=60, label="", **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except DataException as e:
            if "exceeding access rate" in str(e) and attempt < retries:
                wait = min(base_delay * attempt, max_wait)
                print(f"[rate-limit] {label} attempt {attempt}/{retries}, retry in {wait}s")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < retries:
                wait = min(base_delay * attempt, max_wait)
                print(f"[retry] {label} attempt {attempt}/{retries} error: {e}, retry in {wait}s")
                time.sleep(wait)
            else:
                raise


def login():
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)
    if not data.get("status"):
        raise SystemExit(f"Login failed: {data}")
    return smart_api


# ============================================================
# TELEGRAM INPUT (webhook -> website storage -> API)
# ============================================================

def assign_options(idx_in, opts):
    """Option kya index no chhe: prefix hoy to e, nahi to strike index ni nazdik na range thi."""
    refs = {}
    for name, d in idx_in.items():
        if d.get("low") is not None:
            refs[name] = (d["low"] + d["high"]) / 2
        elif d.get("price") is not None:
            refs[name] = d["price"]
    out = {i["name"]: [] for i in INDEXES}
    for o in opts:
        name = o["prefix"]
        if not name and refs:
            name = min(refs, key=lambda n: abs(o["strike"] - refs[n]) / refs[n])
        if name in out:
            out[name].append(o)
    for name in out:
        out[name].sort(key=lambda o: (o["type"], o["strike"]))
    return out


def collect_inputs():
    """Tamari website (PythonAnywhere) par webhook thi save thayelo aajno input."""
    data = {}
    try:
        r = requests.get(f"{WEBSITE_URL}/premarket/api/{FIB_API_SECRET}", timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[WARN] premarket input fetch failed: {e}")
    idx_in = data.get("index", {})
    return idx_in, assign_options(idx_in, data.get("options", []))


def inputs_complete(idx_in, opts_by_idx):
    for i in INDEXES:
        d = idx_in.get(i["name"], {})
        if d.get("low") is None or d.get("price") is None:
            return False
        if len(opts_by_idx.get(i["name"], [])) < OPTIONS_PER_INDEX:
            return False
    return True


def wait_for_inputs():
    h, m = map(int, INPUT_DEADLINE_IST.split(":"))
    deadline = datetime.now(IST).replace(hour=h, minute=m, second=0, microsecond=0)
    while True:
        idx_in, opts_by_idx = collect_inputs()
        if inputs_complete(idx_in, opts_by_idx):
            print("[INFO] Badho input aavi gayo.")
            return idx_in, opts_by_idx
        if datetime.now(IST) >= deadline:
            print("[INFO] Deadline aavi gai, je input chhe te vapru chhu.")
            return idx_in, opts_by_idx
        time.sleep(10)


# ============================================================
# ANGEL ONE: option chain + Greeks + LTP
# ============================================================

def load_master():
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    return pd.DataFrame(resp.json())


def get_expiry_options(master_df, name):
    opts = master_df[
        (master_df["name"] == name)
        & (master_df["exch_seg"] == "NFO")
        & (master_df["instrumenttype"] == "OPTIDX")
    ].copy()
    if opts.empty:
        return None, None
    opts["expiry_dt"] = pd.to_datetime(opts["expiry"], format="%d%b%Y", errors="coerce")
    opts = opts.dropna(subset=["expiry_dt"])
    today = pd.Timestamp.now().normalize()
    upcoming = opts[opts["expiry_dt"] >= today]
    if upcoming.empty:
        return None, None
    nearest = upcoming["expiry_dt"].min()
    opts = upcoming[upcoming["expiry_dt"] == nearest].copy()
    opts["strike_val"] = pd.to_numeric(opts["strike"], errors="coerce") / 100.0
    opts = opts.dropna(subset=["strike_val"])
    return nearest, opts


def find_row(opts_df, strike, opt_type):
    m = opts_df[(opts_df["strike_val"].round(2) == round(strike, 2)) & (opts_df["symbol"].str.endswith(opt_type))]
    if m.empty:
        return None
    r = m.iloc[0]
    return {"token": str(r["token"]), "symbol": r["symbol"]}


def fetch_greeks(smart_api, opt_name, expiry_dt):
    try:
        resp = safe_call(
            smart_api.optionGreek,
            {"name": opt_name, "expirydate": expiry_dt.strftime("%d%b%Y").upper()},
            label=f"greeks({opt_name})",
        )
        rows = resp.get("data") or []
        print(f"[DEBUG] greeks {opt_name}: status={resp.get('status')} rows={len(rows)}")
        out = {}
        for r in rows:
            try:
                out[(round(float(r.get("strikePrice")), 2), r.get("optionType"))] = {
                    "delta": r.get("delta"), "theta": r.get("theta"),
                }
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"[WARN] greeks failed for {opt_name}: {e}")
        return {}


def get_greek(greeks_map, strike, opt_type):
    g = greeks_map.get((round(strike, 2), opt_type))
    if g:
        return g
    for (s, t), v in greeks_map.items():
        if t == opt_type and abs(s - strike) < 1:
            return v
    return {"delta": None, "theta": None}


def get_quote(smart_api, exchange, symbol, token):
    """(ltp, last_close). Pre-market ma ltp na male to close vapray."""
    try:
        resp = safe_call(smart_api.ltpData, exchange, symbol, token,
                         retries=3, base_delay=3, label=f"ltp({symbol})")
        d = resp.get("data") or {}
        ltp = float(d["ltp"]) if d.get("ltp") not in (None, "") else None
        close = float(d["close"]) if d.get("close") not in (None, "") else None
        if ltp is not None and ltp <= 0:
            ltp = None
        return ltp, close
    except Exception as e:
        print(f"[WARN] LTP failed for {symbol}: {e}")
        return None, None


# ============================================================
# BIAS + BEST ENTRY / TARGET / SL
# ============================================================

def index_bias(price, lo, hi):
    levels = [(fib(r, lo, hi), a) for r, a in BIAS_LEVELS]
    return min(levels, key=lambda x: abs(price - x[0]))[1]


def index_scenarios(price, lo, hi):
    rng = hi - lo
    return {
        "CALL": {"target": round(hi + TARGET_EXT * rng, 2), "sl": round(price - SL_RANGE_PCT * rng, 2)},
        "PUT":  {"target": round(lo - TARGET_EXT * rng, 2), "sl": round(price + SL_RANGE_PCT * rng, 2)},
    }


def option_plan(lo, hi, ltp, delta, theta, spot, scen, opt_type, bias):
    plan = {"best_entry": max(round(fib(ENTRY_RATIO, lo, hi), 2), 0.05)}
    if ltp is None:
        plan["status"] = "LTP na malyo"
        return plan
    try:
        d = float(delta)
        decay = abs(float(theta)) * HOLD_DAY_FRACTION
    except (TypeError, ValueError):
        plan["status"] = "Greeks na malya"
        return plan

    target = ltp + d * (scen["target"] - spot) - decay
    sl = max(ltp + d * (scen["sl"] - spot) - decay, 0.05)
    plan["target"] = round(target, 2)
    plan["sl"] = round(sl, 2)

    risk, reward = ltp - sl, target - ltp
    rr = round(reward / risk, 2) if risk > 0 and reward > 0 else None
    plan["rr"] = rr

    want = "CALL" if opt_type == "CE" else "PUT"
    if bias == "WATCH" or bias is None:
        plan["status"] = "WAIT (confirmation ni raah)"
    elif bias != want:
        plan["status"] = f"SKIP (index bias {bias})"
    elif rr is None or rr < MIN_RR:
        plan["status"] = f"SKIP (RR {rr if rr is not None else '-'} < {MIN_RR})"
    else:
        plan["status"] = f"TRADE (RR 1:{rr})"
    return plan


# ============================================================
# MAIN
# ============================================================

def main():
    smart_api = login()
    time.sleep(3)
    master_df = load_master()
    today_ist = datetime.now(IST).date()

    # --- Telegram input ni raah jota jota Greeks fetch kari lo ---
    prefetch = {}
    for idx in INDEXES:
        try:
            expiry_dt, opts_df = get_expiry_options(master_df, idx["opt_name"])
        except Exception as e:
            print(f"[WARN] expiry error {idx['name']}: {e}")
            expiry_dt, opts_df = None, None
        greeks = fetch_greeks(smart_api, idx["opt_name"], expiry_dt) if expiry_dt is not None else {}
        prefetch[idx["name"]] = (expiry_dt, opts_df, greeks)
        time.sleep(2)

    idx_in, opts_by_idx = wait_for_inputs()

    result = {"generated_at": datetime.now(IST).isoformat(), "indexes": []}

    for idx in INDEXES:
        name = idx["name"]
        entry = {"name": name}
        d = idx_in.get(name, {})
        lo, hi = d.get("low"), d.get("high")
        if lo is None or hi is None or hi <= lo:
            entry["error"] = "VAL/VAH na malya (format: NIFTY VAL 22522 VAH 22590)"
            result["indexes"].append(entry)
            continue

        price, price_src = d.get("price"), "telegram"
        if price is None:
            time.sleep(1)
            ltp, close = get_quote(smart_api, "NSE", name, idx["token"])
            price = ltp if ltp is not None else close
            price_src = "angel_ltp" if price is not None else None
        if price is None:
            entry["error"] = "Pre-open price na malyo (format: NIFTY 22525.65)"
            result["indexes"].append(entry)
            continue

        bias = index_bias(price, lo, hi)
        scen = index_scenarios(price, lo, hi)
        entry.update({
            "low": lo, "high": hi, "price": price, "price_source": price_src,
            "bias": bias,
            "best_entry": round(fib(ENTRY_RATIO, lo, hi), 2),
            "scenarios": scen,
        })
        if bias in scen:
            entry["target"], entry["sl"] = scen[bias]["target"], scen[bias]["sl"]

        expiry_dt, opts_df, greeks_map = prefetch[name]
        if expiry_dt is not None:
            entry["expiry"] = expiry_dt.strftime("%d-%b-%Y")
            entry["is_expiry_day"] = (expiry_dt.date() == today_ist)

        entry["options"] = []
        user_opts = opts_by_idx.get(name, [])
        if not user_opts:
            entry["option_error"] = "Telegram thi options na malya (format: 22550CE VAL 120 VAH 160)"
        elif opts_df is None:
            entry["option_error"] = "Angel master ma options na malya"
        else:
            for o in user_opts:
                base = {"type": o["type"], "strike": o["strike"], "low": o["low"], "high": o["high"]}
                row = find_row(opts_df, o["strike"], o["type"])
                if not row:
                    base["error"] = "strike Angel ma na malyo"
                    entry["options"].append(base)
                    continue
                time.sleep(1)
                ltp, close = get_quote(smart_api, "NFO", row["symbol"], row["token"])
                if o["ltp_override"] is not None:
                    ltp = o["ltp_override"]
                elif ltp is None:
                    ltp = close
                g = get_greek(greeks_map, o["strike"], o["type"])
                plan = option_plan(o["low"], o["high"], ltp, g["delta"], g["theta"],
                                   price, scen["CALL" if o["type"] == "CE" else "PUT"], o["type"], bias)
                base.update({"symbol": row["symbol"], "ltp": ltp,
                             "delta": g["delta"], "theta": g["theta"], **plan})
                entry["options"].append(base)

        result["indexes"].append(entry)

    with open(OUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Done. Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()