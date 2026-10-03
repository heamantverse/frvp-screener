"""
Pre-market Nifty & BankNifty options alert.

Flow:
  1. Login + Greeks (delta/theta) Angel One thi pehla thi fetch.
  2. Website API thi aajno Telegram input vancho (9:10 sudhi raah, badho aavi jay to vahelu).
  3. Index na VAL/VAH par fib ladder: Entry level, Target 1-2-3 (next levels), SL (opposite next level).
     - Candle level ne upar thi niche aavi ne pachhu upar close thay  -> CALL
     - Candle level ne niche thi upar jai ne pachhu niche close thay  -> PUT
     - Price level ni nazdik hoy to banne chance, nahi to niche no support (CALL) ane upar no resistance (PUT).
  4. Option na Entry/Target/SL = index levels ne Delta thi option price ma convert (theta decay ghatadi ne).
  5. index_alert.json lakho -> make_index_alert_pdf.py PDF banave.

Telegram input (VAL VAH = pehlo nano, biju motu number):
    NIFTY 22522 22590
    NIFTY 22525.65            <- pre-open price
    22550CE 120 160           <- option VAL VAH
"""

import os
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
# CONFIG — badha points aa j jagya e badlo
# ============================================================
# Fib ladder (ratio alert ma dekhatu nathi, faqat naam + price)
LEVELS = [
    (1.618,  "Golden Reversal T1"),
    (1.272,  "Potential Target 1"),
    (1.0,    "Break up"),
    (0.75,   "Potential Sell Reversal"),
    (0.618,  "Golden Reversal"),
    (0.5,    "Mid Zone"),
    (0.382,  "Reaction Zone"),
    (0.25,   "Potential Buy Reversal"),
    (0.0,    "Breakdown"),
    (-0.272, "Day Low"),
    (-0.618, "Bounce Back"),
    (-1.618, "Potential Target 2"),
]
BEST_ENTRY_RATIO = -0.272        # "Best Entry" line (Day Low)
NEAR_PCT_OF_RANGE = 0.10         # price level thi range na 10% ni andar hoy to "nazdik"
NEAR_MIN_PCT_OF_PRICE = 0.0001
TARGET_COUNT = 3
HOLD_DAY_FRACTION = 0.2          # Theta decay ketla divas no ganvo (~1 kalak = 0.2 divas) - tamari holding pramane badlo
OPTIONS_PER_INDEX = 4            # CE ATM, CE ITM, PE ATM, PE ITM
MID_RATIO = 0.5

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
# FIB LADDER -> ENTRY / TARGET 1-2-3 / SL
# ============================================================

def build_ladder(lo, hi):
    rng = hi - lo
    ladder = [{"ratio": r, "name": n, "price": round(lo + r * rng, 2)} for r, n in LEVELS]
    ladder.sort(key=lambda x: x["price"])
    return ladder


def _lv(l):
    return {"name": l["name"], "price": l["price"]}


def call_plan_at(ladder, i):
    """Candle level ne upar thi niche aavi ne pachhu upar close thay -> CALL."""
    lv = ladder[i]
    return {
        "side": "CALL",
        "level_name": lv["name"],
        "entry": lv["price"],
        "trigger": f"{lv['name']} ({lv['price']}) ni upar thi niche aavi ne candle pachhu upar close thay to CALL",
        "targets": [_lv(l) for l in ladder[i + 1:i + 1 + TARGET_COUNT]],
        "sl": _lv(ladder[i - 1]) if i > 0 else None,
    }


def put_plan_at(ladder, i):
    """Candle level ne niche thi upar jai ne pachhu niche close thay -> PUT."""
    lv = ladder[i]
    lower = ladder[max(0, i - TARGET_COUNT):i]
    return {
        "side": "PUT",
        "level_name": lv["name"],
        "entry": lv["price"],
        "trigger": f"{lv['name']} ({lv['price']}) ni niche thi upar jai ne candle pachhu niche close thay to PUT",
        "targets": [_lv(l) for l in reversed(lower)],
        "sl": _lv(ladder[i + 1]) if i + 1 < len(ladder) else None,
    }


def build_plans(price, lo, hi):
    ladder = build_ladder(lo, hi)
    rng = hi - lo
    tol = max(NEAR_PCT_OF_RANGE * rng, price * NEAR_MIN_PCT_OF_PRICE)

    dists = [abs(price - l["price"]) for l in ladder]
    ni = min(range(len(ladder)), key=lambda k: dists[k])
    near = _lv(ladder[ni]) if dists[ni] <= tol else None

    if near:
        call_i = put_i = ni
    else:
        below = [k for k, l in enumerate(ladder) if l["price"] < price]
        above = [k for k, l in enumerate(ladder) if l["price"] > price]
        call_i = max(below) if below else None
        put_i = min(above) if above else None

    plans = {
        "CALL": call_plan_at(ladder, call_i) if call_i is not None else None,
        "PUT": put_plan_at(ladder, put_i) if put_i is not None else None,
    }

    mid = next(l["price"] for l in ladder if abs(l["ratio"] - MID_RATIO) < 1e-9)
    if near:
        suggestion = (f"Price {near['name']} ({near['price']}) ni nazdik chhe: banne chance. "
                      f"Candle close thi decide karo.")
    elif price > mid:
        suggestion = "Price Mid Zone ni upar chhe: CALL side vadhu preferred (support par rejection). PUT fakt resistance rejection par."
    else:
        suggestion = "Price Mid Zone ni niche chhe: PUT side vadhu preferred (resistance par rejection). CALL fakt support rejection par."

    best_entry = next(l["price"] for l in ladder if abs(l["ratio"] - BEST_ENTRY_RATIO) < 1e-9)
    return {"ladder": ladder, "near": near, "plans": plans, "suggestion": suggestion, "best_entry": best_entry}


def option_plan(o, ltp, delta, theta, spot, idx_plan):
    """Index plan na levels ne Delta thi option price ma convert."""
    out = {"best_entry": max(round(fib(BEST_ENTRY_RATIO, o["low"], o["high"]), 2), 0.05)}
    if ltp is None:
        out["status"] = "LTP na malyo"
        return out
    if not idx_plan:
        out["status"] = "Index level na malyo"
        return out
    try:
        d = float(delta)
        decay = abs(float(theta)) * HOLD_DAY_FRACTION
    except (TypeError, ValueError):
        out["status"] = "Greeks na malya"
        return out

    def at(level):
        return ltp + d * (level - spot)

    entry = max(round(at(idx_plan["entry"]), 2), 0.05)
    targets = [max(round(at(t["price"]) - decay, 2), 0.05) for t in idx_plan["targets"]]
    sl = max(round(at(idx_plan["sl"]["price"]) - decay, 2), 0.05) if idx_plan["sl"] else None

    out.update({"entry": entry, "targets": targets, "sl": sl})
    status = "Candle close ni raah"
    if sl is not None and len(targets) >= 2 and entry > sl and targets[1] > entry:
        status += f" | RR(T2) 1:{round((targets[1] - entry) / (entry - sl), 1)}"
    out["status"] = status
    return out


# ============================================================
# MAIN
# ============================================================

def main():
    smart_api = login()
    time.sleep(3)
    master_df = load_master()
    today_ist = datetime.now(IST).date()

    # Input ni raah jota jota Greeks fetch kari lo
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
            entry["error"] = "VAL/VAH na malya (format: NIFTY 22522 22590)"
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

        built = build_plans(price, lo, hi)
        plans = built["plans"]
        entry.update({
            "low": lo, "high": hi, "price": price, "price_source": price_src,
            "best_entry": built["best_entry"], "near": built["near"],
            "plans": plans, "suggestion": built["suggestion"],
        })

        expiry_dt, opts_df, greeks_map = prefetch[name]
        if expiry_dt is not None:
            entry["expiry"] = expiry_dt.strftime("%d-%b-%Y")
            entry["is_expiry_day"] = (expiry_dt.date() == today_ist)

        entry["options"] = []
        user_opts = opts_by_idx.get(name, [])
        if not user_opts:
            entry["option_error"] = "Telegram thi options na malya (format: 22550CE 120 160)"
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
                plan = option_plan(o, ltp, g["delta"], g["theta"], price,
                                   plans["CALL" if o["type"] == "CE" else "PUT"])
                base.update({"symbol": row["symbol"], "ltp": ltp,
                             "delta": g["delta"], "theta": g["theta"], **plan})
                entry["options"].append(base)

        result["indexes"].append(entry)

    with open(OUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Done. Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()