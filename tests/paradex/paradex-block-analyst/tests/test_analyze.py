#!/usr/bin/env python3
"""analyze.py on real blocks with every venue's market stubbed.
Run: python3 tests/test_analyze.py"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import urllib.error
from urllib.parse import parse_qs, urlparse

from _harness import Tally, block
import analyze as az

t = Tally()


def deribit_ticker(name):
    if "83500" in name or "80000" in name:
        mark, delta = 0.0100, -0.3
    else:
        mark, delta = 0.0050, 0.2
    return {"result": {"mark_price": mark, "best_bid_price": mark - 0.0002, "best_ask_price": mark + 0.0002,
                       "mark_iv": 40.0, "greeks": {"delta": delta, "vega": 10.0, "gamma": 0.0001, "theta": -20.0},
                       "index_price": 84000.0, "underlying_price": 84000.0}}


MISSING_TRADE = set()


def market(url, unlisted=()):
    """A canned answer per venue endpoint."""
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    name = q.get("instrument_name") or q.get("instId") or q.get("market") or ""
    if name in unlisted:
        raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
    if u.path.endswith("/ticker") and "deribit" in u.netloc:
        return deribit_ticker(name)
    if u.path.endswith("get_last_trades_by_instrument_and_time"):
        legs = {l["instrument"]: l for l in block("DBT:BLOCK-302780")["legs"]}
        leg = legs[name]
        if name in MISSING_TRADE:
            return {"result": {"trades": []}}
        return {"result": {"trades": [{"trade_id": "other", "mark_price": 9},
                                      {"trade_id": leg["leg_id"], "mark_price": float(leg["price"]) - 0.0001,
                                       "iv": 41.5}]}}
    if u.path.endswith("markets/summary"):
        return {"results": [{"mark_price": "7.0" if "2200" in name else "14.0", "bid": "6", "ask": "8",
                             "mark_iv": "0.5", "greeks": {"delta": "-0.2", "vega": "1.5", "gamma": "0.001"}}]}
    if "derive" in u.netloc:
        mark = 20 if "2475" in name else 9
        return {"result": {"M": str(mark), "b": str(mark - 1), "a": str(mark + 1), "I": "2505",
                           "option_pricing": {"d": "-0.35", "g": "0.004", "v": "0.7", "t": "-7", "i": "0.43"}}}
    if "okx" in u.netloc:
        if "mark-price" in u.path:
            return {"code": "0", "data": [{"markPx": "0.0011"}]}
        if "market/ticker" in u.path:
            return {"code": "0", "data": [{"bidPx": "0.001", "askPx": "0.0012"}]}
        return {"code": "0", "data": [{"instId": "BTC-USD-261014-83750-C", "markVol": "0.23", "deltaBS": "0.34",
                                       "gammaBS": "0.0009", "vegaBS": "6.3", "thetaBS": "-79", "fwdPx": "83584"}]}
    raise AssertionError(f"unexpected fetch {url}")


def analyse(b, history=None, unlisted=()):
    """(exit code, stdout, result json) for one block."""
    az.WARN.clear()
    az.UNLISTED.clear()
    saved = az._open_json
    az._open_json = lambda url, timeout=az.TIMEOUT_S: market(url, unlisted)
    try:
        with tempfile.TemporaryDirectory() as d:
            json.dump(b, open(os.path.join(d, "block.json"), "w"))
            json.dump(history or {"count": 2, "by_venue": {"DBT": 2}, "days": 7}, open(os.path.join(d, "history.json"), "w"))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = az._run(types.SimpleNamespace(dir=d, render=True))
            text = out.getvalue()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                az._run(types.SimpleNamespace(dir=d, render=False))
            return code, text, json.loads(out.getvalue())
    finally:
        az._open_json = saved


# Deribit: the mark Deribit recorded on each leg's own trade
code, text, r = analyse(block("DBT:BLOCK-302780"))
t.ok(code == 0, "a Deribit block is the answer")
t.ok(r["marks_at_trade"] and r["how"] == "own", "marks come from the legs' own trades")
t.ok(r["struct"] == "Iron Condor" and r["unit"] == 10, "iron condor ×10")
t.ok(r["fill_net"] < 0 and "| Seller | Recd" in text, "a credit condor is a Seller who received")
t.ok("mark at trade" in text and "bps" in text, f"coin offset against the mark at trade: {text.splitlines()[0]}")
t.ok("41.5v" in text, "IV at the trade, from Deribit's own trade")
t.ok("| History | 2 other same-structure block(s) on the options tape, 7d (Deribit 2) |" in text, "history row")
t.ok("Deribit direct" in text, "venue and route")
# Δ = 10 × (+1×-0.3 [80kP] −1×0.2 [84kC] −1×-0.3 [83.5kP] +1×0.2 [87kC]) = 0
t.ok(r["net_greeks"]["delta"] == 0 and r["net_greeks"]["vega"] == 0, f"net greeks: {r['net_greeks']}")

# one leg Deribit's trades do not answer for moves the whole package to live
MISSING_TRADE.add("BTC-16OCT26-87000-C")
code, text, r = analyse(block("DBT:BLOCK-302780"))
MISSING_TRADE.clear()
t.ok(not r["marks_at_trade"] and all(l["mark"] == l["tkr"]["mark"] for l in r["legs"]),
     "never a mix of marks at trade and live marks")
t.ok("could not be read for every leg" in text and "mark at trade" not in text, "and says so")

# Paradex: the live mark, in percent
code, text, r = analyse(block("PRDX:1791942177030201709226870000"))
t.ok(code == 0 and r["quote"] == "USD" and "%" in r["offset"]["txt"], "Paradex fill in percent")
t.ok("live mark" in text and "Paradex's live mark" in text, "says the mark is live")
t.ok("via Paradigm" in text, "a Paradigm-routed block says so")
t.ok("50v" in text, "Paradex's decimal IV is scaled to vol points")

# Derive and OKX: their own live markets
code, text, r = analyse(block("DRV:01a1388e-cb52-7f72-bf85-983d5fb212e4"))
t.ok(code == 0 and r["how"] == "derive" and "Derive's live mark" in text, "Derive on Derive")
t.ok("43v" in text and "2475P 19/21" in text and "%" in r["offset"]["txt"],
     "Derive's compact ticker fields read")
code, text, r = analyse(block("OKX:4007982741313376256"))
t.ok(r["how"] == "okx" and r["quote"] == "BTC" and "bps" in r["offset"]["txt"], "OKX in coin, bps")
t.ok("Δ -0.11 BTC" in text, f"OKX BS delta for a sold 0.31 call: {text.splitlines()[4:6]}")

# any other venue: the same option on Deribit, in USD
bl = dict(block("DBT:BLOCK-302780"), venue="BLSH", block_id="b1")
code, text, r = analyse(bl)
t.ok(r["how"] == "deribit" and r["quote"] == "USD", "Bullish against Deribit in USD")
leg = next(l for l in r["legs"] if l["cp"] != "FUT")
t.ok(abs(leg["mark"] - leg["tkr"]["mark"] * 84000.0) < 1e-6 and leg["fill"] == leg["price_usd"],
     "inverse Deribit mark × index against the fill's USD price")
t.ok("benchmarked on Deribit" in text and "(Deribit)" in text, "says where it was benchmarked")

code, text, r = analyse(bl, unlisted={"BTC-16OCT26-80000-P"})
t.ok("Deribit lists no 80kP" in text and "Rerun" not in text, "an unlisted leg is not a retry")
t.ok("net unavailable" in text, "no net greeks with a leg missing")

# what the venues answer never closes the warnings comment
az.WARN.clear()
text, _ = az.render(dict(r, warnings=["a ---> b", "c -- d"]))
t.ok(text.count("-->") == 1 and text.rstrip().endswith("-->"), "a warning cannot end the comment early")

# what an agent has to finish
code, text, r = analyse({"venue": "DBT", "block_id": "h", "underlying": "BTC",
                         "legs": [{"kind": "PERP", "side": "BUY", "size": "1", "instrument": "BTC-PERPETUAL"}]})
t.ok(code == 1 and "## For the agent" in text, "no option legs hands off")
t.ok(analyse(dict(block("DBT:BLOCK-302780"), complete=False))[1].count("incomplete") == 1,
     "an incomplete block says so")

# a crash after the block is read hands the block on, never a traceback
with tempfile.TemporaryDirectory() as d:
    json.dump(block("DBT:BLOCK-302780"), open(os.path.join(d, "block.json"), "w"))
    saved, az._run = az._run, lambda a: 1 / 0
    sys.argv = ["analyze.py", "--dir", d, "--render"]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = az.main()
    finally:
        az._run = saved
    t.ok(code == 1 and "## For the agent" in out.getvalue() and "BLOCK-302780" in out.getvalue(),
         "a crash hands the block to the agent")

t.done()
