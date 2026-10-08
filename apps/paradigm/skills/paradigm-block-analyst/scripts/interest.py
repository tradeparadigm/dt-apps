#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
interest.py — one line on why an analysed block matters to THIS user.

It asks JEV, through the sidecar's relay, what the taker of the block is
betting on, which needs nothing but the block. With the user's own cleared
blocks from Paradigm (their credentials, signed through the credential proxy
like the paradigm-api helper does) it also asks how the block relates to that
history. It prints at most one line for the block: nothing at all when it has
nothing sure to say or when the relay or JEV is missing, and without the
history part when there are no Paradigm credentials or no history. It never
fails the analysis; analyze.sh appends whatever it prints.

    uv run scripts/interest.py --fill-csv <dir>/fill.csv [--debug] [--print-request]

--debug writes what it did (never a secret) to stderr. --print-request prints
the JEV request and stops, which is what to paste into the playground.
"""
import argparse
import base64
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SIDECAR = os.environ.get("TERMINAL_SIDECAR_URL") or "http://127.0.0.1:8081"
# The relay has its own port, bound to 127.0.0.1 only; the main port refuses it.
JEV_URL = os.environ.get("TERMINAL_JEV_URL") or "http://127.0.0.1:8091/api/jev"
JEV_MODEL = os.environ.get("JEV_MODEL") or "jev-latest"
HISTORY_DAYS = 90
HISTORY_ROWS = 200
TIMEOUT_S = 8
DEBUG = False

# What the taker is betting on: about the block alone, so it is asked for every
# block, with or without the user's history. Price and volatility are two
# questions, not one: JEV reads one fact at a time sharply and blurs two asked
# together. Playground, legs named bought or sold: singles, straddles,
# strangles and a risk reversal 94-100% on both; spreads 98% on volatility but
# a coin toss on price, which spread_price_bet works out instead.
BET_QUESTIONS = {
    "trade_price_bet": {
        "type": "choice",
        "instructions": (
            "Which way does the taker's `trade` gain from the price of its coin? Go leg by leg: "
            "each says whether the taker bought or sold it. Bought calls and sold puts gain when "
            "the price rises; sold calls and bought puts gain when it falls. Legs that pull both "
            "ways equally, like a call and a put bought together at the same strike, are neither."),
        "criteria": {
            "up": "It gains mainly if the price rises.",
            "down": "It gains mainly if the price falls.",
            "neither": "It has no clear price direction.",
        },
    },
    "trade_vol_bet": {
        "type": "choice",
        "instructions": (
            "Does the taker's `trade` gain if volatility rises or falls? Go leg by leg: each says "
            "whether the taker bought or sold it. Bought options (calls or puts) gain when "
            "volatility rises; sold options gain when it falls. One option bought and another "
            "sold, as in a spread, is neither."),
        "criteria": {
            "rises": "It is mainly bought options: it gains if volatility rises.",
            "falls": "It is mainly sold options: it gains if volatility falls.",
            "neither": "It has no clear volatility direction.",
        },
    },
}

# About the block against the user's own history, asked only when there is one.
# your_kind_of_trade: v4 tested in the playground (each level within 0.15 of its
# target at 88-97% confidence), then v5: the top level says what "similar" means
# (neighbouring strikes and expiries), since a block a strike or a week from the
# user's own split between 2 and 3. still_holds_it is untested.
HISTORY_QUESTIONS = {
    "your_kind_of_trade": {
        "type": "score",
        "instructions": (
            "How closely does the trade in `trade` resemble what this user trades, judging only "
            "by `trade_history`? Check the coin first (the first word of each description): if "
            "they have never traded it, nothing else counts."),
        "criteria": [
            "They have never traded this coin. The structure does not matter.",
            "They trade this coin, but have never traded this structure on it. Structures differ "
            "by name: a straddle is not a risk reversal, and a single put or call is not a spread.",
            "They have traded this structure on this coin, but far from where they trade it: this "
            "trade's expiry is more than a month after the latest they have traded, or one of its "
            "strikes is outside the lowest to highest they have used.",
            "They have traded this structure on this coin at similar strikes and expiries. Similar "
            "includes neighbouring strikes and expiries a few weeks apart, and this very trade.",
        ],
    },
    "still_holds_it": {
        "type": "choice",
        "instructions": (
            "Per `trade_history` only, does the user still have the same trade as `trade` on: the "
            "same coin, structure, strikes and expiry? A later trade in it on the opposite side "
            "closes an earlier one."),
        "criteria": {
            "still_open": "They traded it and have not closed it: no later opposite-side trade, "
                          "or a smaller one.",
            "closed": "They traded it and later closed all of it with opposite-side trades of the "
                      "same total size.",
            "never": "They have not traded this exact trade. Similar strikes or a different "
                     "expiry do not count.",
        },
    },
}

# How sure an answer must be before it is shown. A wrong line costs more than
# no line, so these lean towards silence.
MIN_SCORE_CONFIDENCE = 0.6
MIN_CHOICE_PROBABILITY = 0.7


def debug(msg):
    if DEBUG:
        print(f"interest: {msg}", file=sys.stderr)


def http_json(url, *, method="GET", body=None, headers=None, timeout=TIMEOUT_S):
    """(status, decoded body or None). Never raises: every failure is a reason
    to stay quiet, and the caller logs it."""
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as e:  # noqa: BLE001
        debug(f"{method} {url}: {e}")
        return None, None
    try:
        return status, json.loads(raw)
    except ValueError:
        debug(f"{method} {url}: {status}, not JSON: {raw[:200]!r}")
        return status, None


# ---------------------------------------------------------------- the relay

def relay_available():
    status, health = http_json(f"{SIDECAR}/health", timeout=3)
    caps = (health or {}).get("capabilities") if status == 200 else None
    if not isinstance(caps, list) or "jev" not in caps:
        debug(f"no JEV relay at {SIDECAR} (status {status}, capabilities {caps})")
        return False
    return True


def ask_jev(questions, state):
    body = json.dumps({"model": JEV_MODEL, "questions": questions, "state": state}).encode()
    status, resp = http_json(JEV_URL, method="POST", body=body,
                             headers={"Content-Type": "application/json"}, timeout=25)
    debug(f"JEV answered {status}: {json.dumps(resp)[:2000] if resp is not None else None}")
    if status != 200 or not isinstance(resp, dict):
        return None
    return resp


# ------------------------------------------------- the user's Paradigm trades

def paradigm_credentials():
    """(access var, sign var, host), chosen the way the paradigm-api helper
    chooses them, or None. PARADIGM_SIGN / PARADIGM_ACCESS / HOST override."""
    env = os.environ
    signs = [k for k in env if re.match(r"^CRED_.*PARADIGM", k, re.I) and env[k].startswith("sign-")]
    sign = env.get("PARADIGM_SIGN") or (signs[0] if len(signs) == 1 else None)
    if not sign:
        debug(f"Paradigm signing credential: {len(signs)} found ({', '.join(signs) or 'none'})")
        return None
    stem = re.sub(r"_[^_]+$", "", sign)
    access = env.get("PARADIGM_ACCESS")
    if not access:
        hits = [k for k in env if k.startswith(stem + "_") and env[k].startswith("cred-")]
        if len(hits) != 1:
            debug(f"Paradigm access key under {stem}_*: {len(hits)} found")
            return None
        access = hits[0]
    host = env.get("HOST")
    if not host:
        if re.search("TESTNET", sign, re.I):
            host = "api.testnet.paradigm.trade"
        elif re.search("MAINNET|PROD", sign, re.I):
            host = "api.prod.paradigm.trade"
        else:
            debug(f"{sign} names no environment; set HOST")
            return None
    return access, sign, host


def paradigm_get(creds, target):
    """GET target (path plus query) signed through the credential proxy."""
    access, sign, host = creds
    ts = str(int(time.time() * 1000))
    payload = "\n".join([ts, "GET", target, ""])
    header = "X-Dime-Sign-" + sign.removeprefix("CRED_").lower().replace("_", "-")
    status, body = http_json(f"https://{host}{target}", headers={
        "Authorization": "Bearer " + os.environ[access],
        "Paradigm-API-Timestamp": ts,
        "Paradigm-API-Signature": os.environ[sign],
        header: base64.b64encode(payload.encode()).decode(),
    })
    debug(f"GET {target}: {status}")
    return body if status == 200 else None


def fetch_trades(creds, pages=3):
    """The user's cleared blocks, newest first as Paradigm returns them."""
    rows, target = [], "/v2/drfq/trades/"
    for _ in range(pages):
        body = paradigm_get(creds, target)
        if body is None:
            break
        page = body.get("results") if isinstance(body, dict) else body
        if not isinstance(page, list):
            debug(f"unexpected trades shape: keys {list(body)[:10] if isinstance(body, dict) else type(body)}")
            break
        rows += page
        cursor = body.get("next") if isinstance(body, dict) else None
        if not cursor:
            break
        if cursor.startswith(("/", "http")):
            target = re.sub(r"^https?://[^/]+", "", cursor)
        else:
            target = "/v2/drfq/trades/?" + urllib.parse.urlencode({"cursor": cursor})
    if rows:
        debug(f"{len(rows)} trades; first row's fields: {sorted(rows[0])}")
    return rows


def _first(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, "", []):
            return v
    return None


def _when(v):
    """A date from epoch ms/s or ISO text, or None."""
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        n = float(v)
        return dt.datetime.fromtimestamp(n / 1000 if n > 1e11 else n, dt.timezone.utc)
    if isinstance(v, str):
        try:
            return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


# An instrument name's coin: BTC-30OCT26-110000-C, ETH-PERPETUAL, and the
# strategy form Paradigm prefixes with a code (XB_ETH-23OCT26-2200-P).
_INSTRUMENT_COIN = re.compile(r"(?:^|[_\s/(])([A-Z][A-Z0-9]{1,9})-(?:\d{1,2}[A-Z]{3}\d{2}|PERP)")


def coin_of(*texts):
    """The coin the first of texts that names one names, or None."""
    for text in texts:
        m = _INSTRUMENT_COIN.search(str(text or "").upper())
        if m:
            return m.group(1)
    return None


def with_coin(coin, desc):
    """desc led by its coin, which JEV checks first; Paradigm's descriptions
    ("Put 23 Oct 26 2200") do not carry it."""
    if not coin or desc.upper().split()[:1] == [coin]:
        return desc
    return f"{coin} {desc}"


def _held(package_side, leg_side):
    """'bought' or 'sold': a leg as the holder of the package holds it."""
    flip = str(package_side).upper() == "SELL"
    sold = str(leg_side or "BUY").upper() == "SELL"
    return "sold" if sold != flip else "bought"


def history_rows(trades, exclude_rfq, now):
    """The rows JEV reads: date, description, side, quantity, price. The
    analysed block itself is left out, so a user analysing their own trade is
    not told it is their kind of trade because of that trade."""
    out = []
    for t in trades:
        rfq = str(_first(t, "rfq_id") or "")
        if exclude_rfq and rfq and rfq.removeprefix("r_") == exclude_rfq.removeprefix("r_"):
            continue
        when = _when(_first(t, "traded_at", "executed_at", "created_at", "created", "timestamp"))
        if when and (now - when).days > HISTORY_DAYS:
            continue
        # The DRFQv2 trade object (fill_sources.rows_from_trade reads the same
        # one): legs state the structure, the package side the direction, and a
        # leg is held as the two combined.
        top = "SELL" if str(t.get("side") or "BUY").upper() == "SELL" else "BUY"
        legs = "; ".join(
            f"{_held(top, l.get('side'))} {l.get('instrument_name') or l.get('instrument') or ''}".strip()
            + (f" x{l['quantity']}" if l.get("quantity") not in (None, "") else "")
            for l in (t.get("legs") or []) if isinstance(l, dict)) or None
        strategy = _first(t, "strategy_description")
        desc = _first(t, "description", "structure") or strategy or legs
        if not desc:
            continue
        coin = coin_of(strategy, legs, desc) or (str(_first(t, "base_currency", "underlying") or "").upper() or None)
        # The structure's name, then what was bought and sold: the name for
        # matching structures, the legs for strikes, expiries and direction.
        detail = legs or strategy
        if detail and detail != desc:
            desc = f"{desc}: {detail}" if legs else f"{desc} ({detail})"
        out.append({"date": when.date().isoformat() if when else None, "description": with_coin(coin, str(desc)),
                    "side": _first(t, "side", "direction"), "quantity": _first(t, "quantity", "size", "amount"),
                    "price": _first(t, "price")})
        if len(out) >= HISTORY_ROWS:
            break
    return out


# ------------------------------------------------------------- the block

_LEG = re.compile(r"^(?P<coin>[A-Z0-9]+)-(?P<expiry>\d{1,2}[A-Z]{3}\d{2})-(?P<strike>[\d.]+)-(?P<cp>[CP])$")


def spread_price_bet(legs):
    """'up' or 'down' for a vertical spread, else None. legs: (side, instrument).

    JEV reads a single option, a straddle or a risk reversal leg by leg, but not
    a spread: one bought and one sold of the same kind comes down to which
    strike is higher, and it compares strikes no better than a coin toss even
    with the legs labelled. The answer is certain from the legs, so it is
    worked out here: the lower call or the higher put decides, and that leg
    bought gains on a fall for puts, a rise for calls.
    """
    if len(legs) != 2:
        return None
    parsed = [(side, _LEG.match(name.upper())) for side, name in legs]
    if not all(m for _, m in parsed):
        return None
    (s1, a), (s2, b) = parsed
    if (a["coin"], a["expiry"], a["cp"]) != (b["coin"], b["expiry"], b["cp"]) or s1 == s2 \
            or float(a["strike"]) == float(b["strike"]):
        return None
    calls = a["cp"] == "C"
    lo, hi = sorted(parsed, key=lambda x: float(x[1]["strike"]))
    side = (lo if calls else hi)[0]
    return ("up" if side == "BUY" else "down") if calls else ("up" if side == "SELL" else "down")


def block_trade(fill_csv):
    """(description for JEV, rfq id, coin, spread direction or None) from the
    fill collect_analysis.py wrote. The description leads with the coin and
    names each leg as the taker bought or sold it."""
    with open(fill_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None, None, None, None
    r = rows[0]
    desc = (r.get("DESCRIPTION") or "").strip()
    if not desc:
        return None, None, None, None
    instruments = [(x.get("INSTRUMENT") or "").strip() for x in rows]
    product = (r.get("PRODUCT") or "").split()
    coin = coin_of(*instruments) or (product[0].upper() if product else None)
    # Each row is one leg, and its SIDE is that leg as the taker holds it
    # (analyze_core.legs_from_rows signs legs the same way). Said leg by leg,
    # so a sold spread is not read off one side for the whole package.
    legs, signed = [], []
    for x in rows:
        side = (x.get("SIDE") or "").strip().upper()
        name = (x.get("INSTRUMENT") or "").strip() or ("it" if len(rows) == 1 else "")
        qty = (x.get("QTY") or "").strip()
        if side in ("BUY", "SELL") and name:
            signed.append((side, name))
            legs.append(f"{'bought' if side == 'BUY' else 'sold'} {name}" + (f" x{qty}" if qty else ""))
    text = with_coin(coin, desc) + (": the taker " + "; ".join(legs) if legs else "")
    return text, (r.get("RFQ_ID") or "").strip(), coin, spread_price_bet(signed)


# ---------------------------------------------------------------- the line

def answer(resp, key):
    """One question's answer object, wherever the response keeps it."""
    for container in (resp.get("answers"), resp.get("results"), resp):
        if isinstance(container, dict) and isinstance(container.get(key), dict):
            return container[key]
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _choice(resp, key):
    """The chosen option of a choice answer, or None when JEV is not sure of it."""
    a = answer(resp, key) or {}
    choice = a.get("choice") or a.get("answer")
    if choice and (_num((a.get("probabilities") or {}).get(choice)) or 0) >= MIN_CHOICE_PROBABILITY:
        return choice
    return None


def bet_line(resp, coin, price_bet=None):
    """'The taker is betting ETH falls and volatility rises.', or None.
    price_bet, when the code knows it (a spread), stands in for JEV's."""
    price = {"up": f"{coin or 'the price'} rises", "down": f"{coin or 'the price'} falls"}.get(
        price_bet or _choice(resp, "trade_price_bet"))
    vol = {"rises": "volatility rises", "falls": "volatility falls"}.get(_choice(resp, "trade_vol_bet"))
    parts = [p for p in (price, vol) if p]
    return f"*The taker is betting {' and '.join(parts)}.*" if parts else None


def history_line(resp):
    if _choice(resp, "still_holds_it") == "still_open":
        return "*You opened this on Paradigm and haven't closed it there: this print shows where it trades now.*"

    kind = answer(resp, "your_kind_of_trade") or {}
    score = _num(kind.get("score") if "score" in kind else kind.get("value"))
    conf = _num(kind.get("confidence"))
    if score is None or conf is None or conf < MIN_SCORE_CONFIDENCE:
        return None
    level = round(score)
    if level >= 3:
        return "*Your kind of trade: you've traded this structure on this coin at similar strikes and expiries.*"
    if level == 2:
        return "*Your structure, outside your usual strikes or expiries.*"
    return None


def line_for(resp, coin=None, price_bet=None):
    """One line: the taker's bet, then what the block is to the user. Either
    half is left out when JEV is unsure of it."""
    parts = [p for p in (bet_line(resp, coin, price_bet), history_line(resp)) if p]
    return " ".join(parts) or None


def main(argv=None):
    global DEBUG
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill-csv", required=True)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--print-request", action="store_true")
    a = ap.parse_args(argv)
    DEBUG = a.debug or a.print_request

    trade, rfq, coin, price_bet = block_trade(a.fill_csv)
    if not trade:
        debug("no trade description in the fill")
        return 0
    if not a.print_request and not relay_available():
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    state = {"today": now.date().isoformat(), "trade": trade}
    questions = dict(BET_QUESTIONS)
    if price_bet:
        del questions["trade_price_bet"]
    # The history half needs the user's Paradigm account; the bet does not.
    creds = paradigm_credentials()
    history = history_rows(fetch_trades(creds), rfq, now) if creds else []
    if history:
        state["trade_history"] = json.dumps(history, separators=(",", ":"))
        questions.update(HISTORY_QUESTIONS)
    elif creds:
        debug("no Paradigm history in the last 90 days")
    if a.print_request:
        print(json.dumps({"model": JEV_MODEL, "questions": questions, "state": state}, indent=2))
        return 0
    resp = ask_jev(questions, state)
    line = line_for(resp, coin, price_bet) if resp else None
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — an extra line is never worth failing the analysis
        debug(f"failed: {e!r}")
        sys.exit(0)
