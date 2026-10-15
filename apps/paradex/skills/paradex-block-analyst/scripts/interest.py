#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
interest.py — one line on what the taker of an analysed block is betting on.

It asks JEV, through the sidecar's relay, which way the taker gains on price
and on volatility. It prints at most one line: nothing at all when JEV is not
sure, or when the relay or JEV is missing. It never fails the analysis;
analyze.sh appends whatever it prints.

    uv run scripts/interest.py --block-json <dir>/block.json [--debug] [--print-request]

The questions and thresholds are paradigm-block-analyst's, which were tested
in the JEV playground; keep the two in step.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

SIDECAR = os.environ.get("TERMINAL_SIDECAR_URL") or "http://127.0.0.1:8081"
# The relay has its own port, bound to 127.0.0.1 only; the main port refuses it.
JEV_URL = os.environ.get("TERMINAL_JEV_URL") or "http://127.0.0.1:8091/api/jev"
JEV_MODEL = os.environ.get("JEV_MODEL") or "jev-latest"
TIMEOUT_S = 8
DEBUG = False

# Price and volatility are two questions: JEV reads one fact at a time sharply
# and blurs two asked together. Spreads are a coin toss for it on price, which
# spread_price_bet works out instead.
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

# A wrong line costs more than no line, so this leans towards silence.
MIN_CHOICE_PROBABILITY = 0.7


def debug(msg):
    if DEBUG:
        print(f"interest: {msg}", file=sys.stderr)


def http_json(url, *, method="GET", body=None, headers=None, timeout=TIMEOUT_S):
    """(status, decoded body or None). Never raises."""
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


def spread_price_bet(legs):
    """'up' or 'down' for a vertical spread, else None.

    One option bought and one sold of the same kind, coin and expiry comes
    down to which strike is higher, which JEV compares no better than a coin
    toss. The lower call or the higher put decides.
    """
    opt = core.option_legs(legs)
    if len(opt) != 2:
        return None
    a, b = opt
    if (a["asset"], a["expiry_c"], a["cp"]) != (b["asset"], b["expiry_c"], b["cp"]) \
            or a["sign"] == b["sign"] or a["strike"] == b["strike"]:
        return None
    calls = a["cp"] == "C"
    lo, hi = sorted(opt, key=lambda l: l["strike"])
    decider = lo if calls else hi
    bought = decider["sign"] > 0
    return ("up" if bought else "down") if calls else ("down" if bought else "up")


def describe(block):
    """(description for JEV, coin, spread direction or None). It leads with
    the coin and names each leg as the taker bought or sold it."""
    legs, _ = core.legs_from_block(block)
    opt = core.option_legs(legs)
    if not opt:
        return None, None, None
    coin = (block.get("underlying") or opt[0]["asset"]).upper()
    named = []
    for l in legs:
        verb = "bought" if l["sign"] > 0 else "sold"
        if l["cp"] == "FUT":
            named.append(f"{verb} {l['size']:g} {l['asset']} perpetual")
        else:
            kind = "call" if l["cp"] == "C" else "put"
            named.append(f"{verb} {l['asset']} {l['expiry_c']} {l['strike']:g} {kind} x{l['size']:g}")
    text = f"{coin} {core.struct_name(legs)}: the taker " + "; ".join(named)
    return text, coin, spread_price_bet(legs)


def answer(resp, key):
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
    a = answer(resp, key) or {}
    choice = a.get("choice") or a.get("answer")
    if choice and (_num((a.get("probabilities") or {}).get(choice)) or 0) >= MIN_CHOICE_PROBABILITY:
        return choice
    return None


def bet_line(resp, coin, price_bet=None):
    """'The taker is betting ETH falls and volatility rises.', or None."""
    price = {"up": f"{coin or 'the price'} rises", "down": f"{coin or 'the price'} falls"}.get(
        price_bet or _choice(resp, "trade_price_bet"))
    vol = {"rises": "volatility rises", "falls": "volatility falls"}.get(_choice(resp, "trade_vol_bet"))
    parts = [p for p in (price, vol) if p]
    return f"*The taker is betting {' and '.join(parts)}.*" if parts else None


def build_request(trade, price_bet, now):
    questions = dict(BET_QUESTIONS)
    if price_bet:
        del questions["trade_price_bet"]
    return questions, {"today": now.date().isoformat(), "trade": trade}


def main(argv=None):
    global DEBUG
    ap = argparse.ArgumentParser()
    ap.add_argument("--block-json", required=True)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--print-request", action="store_true")
    a = ap.parse_args(argv)
    DEBUG = a.debug or a.print_request

    with open(a.block_json, encoding="utf-8") as f:
        block = json.load(f)
    trade, coin, price_bet = describe(block)
    if not trade:
        debug("no option legs in the block")
        return 0
    questions, state = build_request(trade, price_bet, dt.datetime.now(dt.timezone.utc))
    if a.print_request:
        print(json.dumps({"model": JEV_MODEL, "questions": questions, "state": state}, indent=2))
        return 0
    if not relay_available():
        return 0
    resp = ask_jev(questions, state)
    line = bet_line(resp, coin, price_bet) if resp else None
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — an extra line is never worth failing the analysis
        debug(f"failed: {e!r}")
        sys.exit(0)
