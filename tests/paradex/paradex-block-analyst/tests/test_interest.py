#!/usr/bin/env python3
"""interest.py: what JEV is asked, and the one line it prints.
Run: python3 tests/test_interest.py"""
import contextlib
import io
import json
import os
import tempfile

from _harness import Tally, block
import core
import interest

t = Tally()


def legs_of(spec):
    """[(side, cp, strike)] → legs on one BTC expiry."""
    return core.legs_from_block({"underlying": "BTC", "legs": [
        {"kind": "OPTION", "option_type": "CALL" if cp == "C" else "PUT", "strike": str(k),
         "expiry": 1792137600000, "side": side, "size": "1", "instrument": f"{k}{cp}"}
        for side, cp, k in spec]})[0]


# a vertical spread's price direction is worked out, never asked
t.ok(interest.spread_price_bet(legs_of([("BUY", "C", 80000), ("SELL", "C", 90000)])) == "up", "bull call spread")
t.ok(interest.spread_price_bet(legs_of([("SELL", "C", 80000), ("BUY", "C", 90000)])) == "down", "bear call spread")
t.ok(interest.spread_price_bet(legs_of([("BUY", "P", 90000), ("SELL", "P", 80000)])) == "down", "bear put spread")
t.ok(interest.spread_price_bet(legs_of([("SELL", "P", 90000), ("BUY", "P", 80000)])) == "up", "bull put spread")
t.ok(interest.spread_price_bet(legs_of([("BUY", "C", 80000), ("BUY", "P", 80000)])) is None, "a straddle is asked")
t.ok(interest.spread_price_bet(legs_of([("BUY", "C", 80000)])) is None, "a single option is asked")

trade, coin, bet = interest.describe(block("PRDX:1791942177030201709226870000"))
t.ok(coin == "ETH" and bet == "up", f"the Paradex put spread was sold high: {bet}")
t.ok(trade.startswith("ETH Put Spread: the taker ") and "sold ETH 23OCT26 2300 put x73" in trade,
     f"each leg named as the taker holds it: {trade}")
q, state = interest.build_request(trade, bet, __import__("datetime").datetime(2026, 10, 14))
t.ok(set(q) == {"trade_vol_bet"} and state["trade"] == trade, "a known direction is not asked again")

ans = {"answers": {"trade_price_bet": {"choice": "down", "probabilities": {"down": 0.9}},
                   "trade_vol_bet": {"choice": "rises", "probabilities": {"rises": 0.65}}}}
t.ok(interest.bet_line(ans, "BTC") == "*The taker is betting BTC falls.*", "an unsure half is left out")
t.ok(interest.bet_line(ans, "BTC", "up") == "*The taker is betting BTC rises.*", "the worked-out direction wins")
t.ok(interest.bet_line({}, "BTC") is None, "nothing sure, no line")

# a sidecar without the jev capability has no relay
saved_http = interest.http_json
interest.http_json = lambda *a, **k: (200, {"capabilities": ["sync"]})
t.ok(interest.relay_available() is False, "no jev capability, no relay")
interest.http_json = lambda *a, **k: (200, {"capabilities": ["jev"]})
t.ok(interest.relay_available() is True, "a jev capability is a relay")
interest.http_json = saved_http

# no relay, no line, exit 0
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "block.json")
    json.dump(block("DBT:BLOCK-302780"), open(p, "w"))
    saved = interest.http_json
    interest.http_json = lambda *a, **k: (None, None)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = interest.main(["--block-json", p])
    finally:
        interest.http_json = saved
    t.ok(code == 0 and out.getvalue() == "", "no relay prints nothing")

    calls = []

    def fake(url, **kw):
        calls.append(url)
        if url.endswith("/health"):
            return 200, {"capabilities": ["jev"]}
        return 200, {"answers": {"trade_vol_bet": {"choice": "falls", "probabilities": {"falls": 0.95}}}}
    interest.http_json = fake
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            interest.main(["--block-json", p])
    finally:
        interest.http_json = saved
    t.ok(out.getvalue() == "*The taker is betting volatility falls.*\n", f"one line: {out.getvalue()!r}")
    t.ok(calls[-1] == interest.JEV_URL, "asks the relay's own port")

t.done()
