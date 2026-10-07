#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
interest.py — one line on why an analysed block matters to THIS user.

It reads the user's own cleared blocks from Paradigm (their credentials, signed
through the credential proxy like the paradigm-api helper does), asks JEV two
questions about the block against that history through the sidecar's relay,
and prints at most one italic line for the block. Nothing at all when it has
nothing to say, or when anything on the way is missing: no relay on this agent,
no Paradigm credentials, no history, JEV not answering. It never fails the
analysis; analyze.sh appends whatever it prints.

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
JEV_MODEL = os.environ.get("JEV_MODEL") or "jev-latest"
HISTORY_DAYS = 90
HISTORY_ROWS = 200
TIMEOUT_S = 8
DEBUG = False

# The two questions. Wording tested in the playground: your_kind_of_trade at
# v4 (each level within 0.15 of its target at 88-97% confidence);
# still_holds_it is untested.
QUESTIONS = {
    "your_kind_of_trade": {
        "type": "score",
        "instructions": (
            "How closely does the trade in `trade` resemble what this user trades, judging only "
            "by `trade_history`? Check the coin first: if they have never traded it, nothing else "
            "counts."),
        "criteria": [
            "They have never traded this coin. The structure does not matter.",
            "They trade this coin, but have never traded this structure on it. Structures differ "
            "by name: a straddle is not a risk reversal, and a single put or call is not a spread.",
            "They have traded this structure on this coin, but this trade's expiry is more than a "
            "month after the latest they have traded, or one of its strikes is outside the lowest "
            "to highest they have used.",
            "They have traded this structure on this coin at similar strikes and expiries, or this "
            "very trade.",
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


def ask_jev(state):
    body = json.dumps({"model": JEV_MODEL, "questions": QUESTIONS, "state": state}).encode()
    status, resp = http_json(f"{SIDECAR}/api/jev", method="POST", body=body,
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
        desc = _first(t, "description", "strategy_description", "structure")
        if not desc:
            legs = t.get("legs") or []
            desc = " / ".join(f"{(l.get('side') or '').lower()} {l.get('instrument_name') or l.get('instrument') or ''}".strip()
                              for l in legs if isinstance(l, dict)) or None
        if not desc:
            continue
        out.append({"date": when.date().isoformat() if when else None, "description": desc,
                    "side": _first(t, "side", "direction"), "quantity": _first(t, "quantity", "size", "amount"),
                    "price": _first(t, "price")})
        if len(out) >= HISTORY_ROWS:
            break
    return out


# ------------------------------------------------------------- the block

def block_trade(fill_csv):
    """(description for JEV, rfq id) from the fill collect_analysis.py wrote."""
    with open(fill_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None, None
    r = rows[0]
    desc = (r.get("DESCRIPTION") or "").strip()
    if not desc:
        return None, None
    side, qty = (r.get("SIDE") or "").strip(), (r.get("QTY") or "").strip()
    text = desc + (f" x{qty}" if qty else "") + (f", taker {side.lower()}" if side else "")
    return text, (r.get("RFQ_ID") or "").strip()


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


def line_for(resp):
    held = answer(resp, "still_holds_it") or {}
    choice = held.get("choice") or held.get("answer")
    probs = held.get("probabilities") or {}
    if choice == "still_open" and (_num(probs.get("still_open")) or 0) >= MIN_CHOICE_PROBABILITY:
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


def main(argv=None):
    global DEBUG
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill-csv", required=True)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--print-request", action="store_true")
    a = ap.parse_args(argv)
    DEBUG = a.debug or a.print_request

    trade, rfq = block_trade(a.fill_csv)
    if not trade:
        debug("no trade description in the fill")
        return 0
    if not a.print_request and not relay_available():
        return 0
    creds = paradigm_credentials()
    if not creds:
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    history = history_rows(fetch_trades(creds), rfq, now)
    if not history:
        debug("no Paradigm history in the last 90 days")
        return 0
    state = {"today": now.date().isoformat(), "trade": trade,
             "trade_history": json.dumps(history, separators=(",", ":"))}
    if a.print_request:
        print(json.dumps({"model": JEV_MODEL, "questions": QUESTIONS, "state": state}, indent=2))
        return 0
    resp = ask_jev(state)
    line = line_for(resp) if resp else None
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — an extra line is never worth failing the analysis
        debug(f"failed: {e!r}")
        sys.exit(0)
