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


LIVE_MARKS = {"80000": 0.0100, "83500": 0.0110, "84000": 0.0050, "87000": 0.0040}


def deribit_ticker(name):
    delta = -0.3 if "83500" in name or "80000" in name else 0.2
    mark = next((m for k, m in LIVE_MARKS.items() if k in name), 0.0050)
    return {"result": {"mark_price": mark, "best_bid_price": mark - 0.0002, "best_ask_price": mark + 0.0002,
                       "mark_iv": 40.0, "greeks": {"delta": delta, "vega": 10.0, "gamma": 0.0001, "theta": -20.0},
                       "index_price": 84000.0, "underlying_price": 84000.0}}


MISSING_TRADE = set()
# When the fixture was captured: every option on it was live then.
NOW_MS = 1791951051702
# The close of the minute candle each venue keeps a mark in.
CANDLE_CLOSE = {"ETH-USD-23OCT26-2200-P": "6.5", "ETH-USD-23OCT26-2300-P": "13.0",
                "BTC-USD-261014-83750-C": "0.0012"}
PERP_DOWN = False


def candle(name, q, start_key, end_key):
    """The one-minute candle the trade printed in, asked for by its window."""
    start = int(q[start_key])
    if name not in CANDLE_CLOSE:
        return []
    return [[start, "1", "1", "1", CANDLE_CLOSE[name], "1"]]


def market(url, unlisted=()):
    """A canned answer per venue endpoint."""
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    name = q.get("instrument_name") or q.get("instId") or q.get("market") or ""
    if name in unlisted:
        raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
    if u.path.endswith("/ticker") and "deribit" in u.netloc:
        if PERP_DOWN and name.endswith("PERPETUAL"):
            raise urllib.error.URLError("down")
        return deribit_ticker(name)
    if u.path.endswith("get_last_trades_by_instrument_and_time"):
        # www keeps about a day of trades; the fixture's are only on history.
        if u.netloc != "history.deribit.com":
            return {"result": {"trades": []}}
        legs = {l["instrument"]: l for l in block("DBT:BLOCK-302780")["legs"]}
        leg = legs[name]
        if name in MISSING_TRADE:
            return {"result": {"trades": []}}
        return {"result": {"trades": [{"trade_id": "other", "mark_price": 9},
                                      {"trade_id": leg["leg_id"], "mark_price": float(leg["price"]) - 0.0001,
                                       "iv": 41.5}]}}
    if u.path.endswith("markets/klines"):
        assert q["price_kind"] == "mark" and int(q["end_at"]) - int(q["start_at"]) == 60_000
        q["start_at"] = str(int(q["start_at"]) - int(q["start_at"]) % 60_000)
        return {"results": candle(q["symbol"], q, "start_at", "end_at")}
    if u.path.endswith("markets/summary"):
        return {"results": [{"mark_price": "7.0" if "2200" in name else "14.0", "bid": "6", "ask": "8",
                             "mark_iv": "0.5", "greeks": {"delta": "-0.2", "vega": "1.5", "gamma": "0.001"}}]}
    if "derive" in u.netloc and u.path.endswith("get_trade_history"):
        legs = {l["instrument"]: l for l in block("DRV:01a1388e-cb52-7f72-bf85-983d5fb212e4")["legs"]}
        leg = legs[name]
        assert int(q["from_timestamp"]) <= leg["traded_at"] <= int(q["to_timestamp"])
        # Another trade in the same seconds comes first.
        return {"result": {"trades": [{"trade_id": "other", "mark_price": "99"},
                                      {"trade_id": leg["leg_id"], "mark_price": "11" if "2475" in name else "4"}]}}
    if "derive" in u.netloc:
        mark = 20 if "2475" in name else 9
        return {"result": {"M": str(mark), "b": str(mark - 1), "a": str(mark + 1), "I": "2505",
                           "option_pricing": {"d": "-0.35", "g": "0.004", "v": "0.7", "t": "-7", "i": "0.43"}}}
    if "okx" in u.netloc:
        if "history-mark-price-candles" in u.path:
            q["start"] = str(int(q["before"]) + 1)
            return {"code": "0", "data": candle(name, q, "start", "after")}
        if "mark-price" in u.path:
            return {"code": "0", "data": [{"markPx": "0.0011"}]}
        if "market/ticker" in u.path:
            return {"code": "0", "data": [{"bidPx": "0.001", "askPx": "0.0012"}]}
        return {"code": "0", "data": [{"instId": "BTC-USD-261014-83750-C", "markVol": "0.23", "deltaBS": "0.34",
                                       "gammaBS": "0.0009", "vegaBS": "6.3", "thetaBS": "-79", "fwdPx": "83584"}]}
    raise AssertionError(f"unexpected fetch {url}")


def analyse(b, history=None, unlisted=(), now_ms=NOW_MS):
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
                code = az._run(types.SimpleNamespace(dir=d, render=True, now_ms=now_ms))
            text = out.getvalue()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                az._run(types.SimpleNamespace(dir=d, render=False, now_ms=now_ms))
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
t.ok("41.5v" in text and "live IV" not in text, "IV at the trade, from Deribit's own trade, not called live")
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
t.ok("could not be read for every leg, so the fill is against the live mark" in text
     and "live mark" in text.splitlines()[0] and "mark at trade" not in text.splitlines()[0],
     "and says so")

# Paradex: in percent
code, text, r = analyse(block("PRDX:1791942177030201709226870000"))
t.ok(code == 0 and r["quote"] == "USD" and "%" in r["offset"]["txt"], "Paradex fill in percent")
t.ok("via Paradigm" in text, "a Paradigm-routed block says so")
t.ok("50v" in text, "Paradex's decimal IV is scaled to vol points")

# Derive and OKX: their own markets
code, text, r = analyse(block("DRV:01a1388e-cb52-7f72-bf85-983d5fb212e4"))
t.ok(code == 0 and r["how"] == "derive" and "mark at trade" in text, "Derive on Derive")
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
t.ok("| Greeks | net unavailable: 80kP no data |" in text, "no net greeks with a leg missing, and only it named")

# what the venues answer never closes the warnings comment
az.WARN.clear()
text, _ = az.render(dict(r, warnings=["a ---> b", "c -- d"]))
t.ok(text.count("-->") == 1 and text.rstrip().endswith("-->"), "a warning cannot end the comment early")

# every venue that keeps a mark at the trade is compared against it
code, text, r = analyse(block("PRDX:1791942177030201709226870000"))
legs = {l["strike"]: l for l in r["legs"]}
t.ok(r["marks_at_trade"] and legs[2200]["mark"] == 6.5 and legs[2300]["mark"] == 13.0,
     "Paradex: the close of the minute's mark candle")
t.ok("mark at trade" in text and "live IV" in text, "Paradex's IV is live and says so")
code, text, r = analyse(block("OKX:4007982741313376256"))
t.ok(r["marks_at_trade"] and r["legs"][0]["mark"] == 0.0012, "OKX: the minute's mark candle")
code, text, r = analyse(block("DRV:01a1388e-cb52-7f72-bf85-983d5fb212e4"))
t.ok(r["marks_at_trade"] and {l["mark"] for l in r["legs"]} == {11.0, 4.0},
     "Derive: the mark on the leg's own trade")

# an option that has since expired has no live market
bl = block("PRDX:1791942177030201709226870000")
code, text, r = analyse(bl, now_ms=max(l["expiry"] for l in bl["legs"]) + 1)
t.ok("_Expired after the trade, with no live quote or greeks: 2200P, 2300P._" in text
     and "No live data" not in text and "Paradex lists no" not in text,
     "an expired leg says it expired, never that its data is missing")
t.ok("live IV 2200P expired" not in text, "an expired leg has no IV to call live")
t.ok("| Greeks | net unavailable: 2200P expired, 2300P expired |" in text
     and "2200P expired / 2300P expired" in text and "| Live | 2200P expired · 2300P expired |" in text,
     "each row names an expired leg as expired")
t.ok(r["marks_at_trade"] and "mark at trade" in text, "and is still compared at the trade")

# an expired leg with no mark candle has no mark at all, and says so
saved_close = dict(CANDLE_CLOSE)
del CANDLE_CLOSE["ETH-USD-23OCT26-2200-P"]
code, text, r = analyse(bl, now_ms=max(l["expiry"] for l in bl["legs"]) + 1)
CANDLE_CLOSE.update(saved_close)
t.ok("n/a vs mark" in text.splitlines()[0] and "so the fill is not compared" in text
     and "against the live mark" not in text, "no mark anywhere: the fill is not compared")

# spot for a coin under a dollar keeps its figures
t.ok(az._spot(0.236) == "0.236" and az._spot(1.37) == "1.37" and az._spot(83611.2) == "83,611",
     "spot keeps four figures under 100")

# no perp quote: spot falls back to the block's own index
PERP_DOWN = True
code, text, r = analyse(block("DBT:BLOCK-302780"))
PERP_DOWN = False
t.ok(r["spot"] == float(block("DBT:BLOCK-302780")["index_price"]), "spot from the block's index")

# a 4xx is an answer and is not retried; a dropped connection is retried once
calls = []


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"result": {"ok": 1}}'


def _open(req, timeout):
    calls.append(req)
    if len(calls) == 1:
        raise urllib.error.URLError("reset")
    return _Resp()


saved = az.urllib.request.urlopen
az.urllib.request.urlopen = _open
try:
    t.ok(az._deribit("ticker", {"instrument_name": "X"}) == {"ok": 1} and len(calls) == 2,
         "a dropped connection is retried once")
    calls.clear()

    def _refuse(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError("u", 400, "bad", None, None)
    az.urllib.request.urlopen = _refuse
    try:
        az._deribit("ticker", {"instrument_name": "X"})
    except urllib.error.HTTPError:
        pass
    t.ok(len(calls) == 1, "a 400 is not retried")
finally:
    az.urllib.request.urlopen = saved

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
