#!/usr/bin/env python3
"""
Unit tests for the fill fallbacks and the Paradex branch — no network.
Run: python3 tests/test_fill_sources.py

The tape trails the market by its hourly sync, so a block analysed minutes after
it printed is not on it. These pin the two other sources collect_analysis.py
resolves from (the injected trade and Paradigm's API), the rows they become, and
that a PRDX block is benchmarked on Paradex rather than Deribit.

The fixtures are real rows from GET /v2/drfq/trade_tape/ on 5 Oct 2026.
"""
import argparse
import base64
import contextlib
import csv
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import types
import importlib.util
from pathlib import Path

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))
import skillpath  # noqa: E402
sys.path.insert(0, str(skillpath.scripts("paradigm-block-analyst")))
sys.path.insert(0, str(skillpath.scripts("paradigm-data-discovery")))

for name in ("boto3", "polars"):
    if importlib.util.find_spec(name) is None:
        sys.modules[name] = types.SimpleNamespace(
            client=lambda *a, **k: None, DataFrame=object, concat=lambda *a, **k: None)
import collect_analysis as ca  # noqa: E402
import fill_sources as fs  # noqa: E402
import analyze as az  # noqa: E402

_p = _f = 0


def ok(cond, msg):
    global _p, _f
    if cond:
        _p += 1
    else:
        _f += 1
        print(f"  ✗ {msg}")


def leg(name, side, price, qty, delta="0.1"):
    return {"delta": delta, "instrument_id": 1, "instrument_name": name, "is_hedge": False,
            "price": price, "product_code": "XB", "quantity": qty, "ratio": "1", "side": side}


ETH_PUT = {  # top-level SELL, leg BUY: the taker SOLD the put
    "id": "bt_3KGXSEpj12Wu1Bh0wnK7YyigJjX", "rfq_id": "r_3KGXRbs7FOrCIqOmAimxVv9dIKG",
    "venue": "PRDX", "kind": "OPTION", "state": "FILLED", "executed_at": 1791184876112.77,
    "side": "SELL", "price": "36.43", "quantity": "1",
    "legs": [leg("ETH-30OCT26-2450-P", "BUY", "36.43", "1", "-0.1838772")],
    "strategy_code": "PT", "description": "Put  30 Oct 26  2450", "quote_currency": "USD",
    "mark_price": "37.08115655", "index_price": "2721.54", "counterparty": None}
PSPD = {
    "id": "bt_3KGz5D5fh0WsAhiZGciyYvaFMVn", "rfq_id": "r_3KGz3x7GPXYrQagjnrb9AvyQu0a",
    "venue": "PRDX", "kind": "OPTION", "state": "FILLED", "executed_at": 1791198507611.97,
    "side": "BUY", "price": "5.59", "quantity": "0.2",
    "legs": [leg("ETH-6OCT26-2700-P", "BUY", "8.86", "0.2"),
             leg("ETH-6OCT26-2680-P", "SELL", "3.27", "0.2")],
    "strategy_code": "PS", "description": "PSpd  6 Oct 26  2700/2680", "quote_currency": "USD",
    "mark_price": "5.0977772", "index_price": "2712.44", "counterparty": None}
RR = {  # a negative package price, sold
    "id": "bt_3KGZOVAtaJCLbNGFlaHjkyMxKNK", "rfq_id": "r_3KGZE4X1Nx36zYKxn5RJbL6hPpt",
    "venue": "DBT", "kind": "OPTION", "state": "FILLED", "executed_at": 1791185833096.43,
    "side": "SELL", "price": "-0.006", "quantity": "12.5",
    "legs": [leg("BTC-26MAR27-88000-P", "SELL", "0.1", "12.5"),
             leg("BTC-26MAR27-90000-C", "BUY", "0.094", "12.5")],
    "strategy_code": "CR", "description": "RRCall  26 Mar 27  88000/90000",
    "quote_currency": "BTC", "mark_price": "-0.0055", "index_price": "86197.82"}
CSTM = {
    "id": "bt_3KG4yEmNQH5B943VRAzt0DA2JvW", "rfq_id": "r_3KG4wLtHlyXtEqD9sYoYlakmKhd",
    "venue": "DBT", "kind": "OPTION", "state": "FILLED", "executed_at": 1791170823979.21,
    "side": "BUY", "price": "0.0017", "quantity": "30",
    "legs": [leg("BTC-6OCT26-86500-C", "BUY", "0.0075", "30"),
             leg("BTC-6OCT26-87500-C", "SELL", "0.0033", "30"),
             leg("BTC-6OCT26-85500-P", "SELL", "0.0029", "30"),
             leg("BTC-6OCT26-83000-P", "BUY", "0.0004", "30")],
    "strategy_code": "CM",
    "description": "Cstm  +1.00  Put  6 Oct 26  83000\n      -1.00  Put  6 Oct 26  85500\n"
                   "      +1.00  Call  6 Oct 26  86500\n      -1.00  Call  6 Oct 26  87500",
    "quote_currency": "BTC", "mark_price": "0.0013", "index_price": "86451.80"}
STRANGLE = {
    "id": "bt_3KFz8BoFJ4ajb2pOWt4tQFhQfwg", "rfq_id": "r_3KFykJTIq8ekWpUmalzI04xVL3N",
    "venue": "DBT", "kind": "OPTION", "state": "FILLED", "executed_at": 1791167942762.27,
    "side": "SELL", "price": "0.0092", "quantity": "300",
    "legs": [leg("ETH-9OCT26-2600-P", "BUY", "0.0033", "300"),
             leg("ETH-9OCT26-2850-C", "BUY", "0.0059", "300")],
    "strategy_code": "SG", "description": "Strangle  9 Oct 26  2600/2850",
    "quote_currency": "ETH", "mark_price": "0.0095", "index_price": "2733.94"}
PERP = {
    "id": "bt_3KGyKiD5oN1KrgNaPcHc2L8RMWV", "rfq_id": "r_3KGyJhv82mBULf7VZo5gwpNo7Z1",
    "venue": "PRDX", "kind": "FUTURE", "state": "FILLED", "executed_at": 1791198137001.95,
    "side": "BUY", "price": "0.09628", "quantity": "758",
    "legs": [leg("DOGE-PERPETUAL", "BUY", "0.0963", "758", None)],
    "strategy_code": "FT", "description": "Future  Perpetual", "quote_currency": "USD",
    "mark_price": "0.09618968", "index_price": "0.10"}

# --- an API trade becomes the tape's per-leg rows -------------------------
rows, pkg = fs.rows_from_trade(ETH_PUT)
ok(len(rows) == 1 and rows[0]["SIDE"] == "SELL",
   f"a SELL of a BUY-leg put is a SOLD put on the row [{rows[0]['SIDE']}]")
ok(rows[0]["PRODUCT"] == "ETH OPTION - PRDX", f"PRODUCT in the tape's form [{rows[0]['PRODUCT']}]")
ok(rows[0]["REF_PRICE"] == "37.08115655", "a single leg's REF_PRICE is the package mark")
ok(rows[0]["QUOTE_CURRENCY"] == "USD", "the API's quote_currency, not a derived one")
ok(rows[0]["BLOCK_TRADE_ID"] == ETH_PUT["id"], "the block id is the trade id")
ok(abs(pkg["ref_net"] + 37.08115655) < 1e-9 and abs(pkg["fill_net"] + 36.43) < 1e-9,
   f"the package nets in the taker's direction [{pkg}]")
ok(pkg["check"], "and the leg nets to the package price")

for trade, want in ((PSPD, ["BUY", "SELL"]), (RR, ["BUY", "SELL"]),
                    (CSTM, ["BUY", "SELL", "SELL", "BUY"]), (STRANGLE, ["SELL", "SELL"])):
    rows, pkg = fs.rows_from_trade(trade)
    ok([r["SIDE"] for r in rows] == want,
       f"{trade['strategy_code']}: the taker's side on each leg {[r['SIDE'] for r in rows]}")
    ok(pkg["check"], f"{trade['strategy_code']}: leg prices net to the package price, "
                     "which is what proves the side combination")
    ok(all(r["REF_PRICE"] == "" for r in rows),
       f"{trade['strategy_code']}: no per-leg mark is invented for a multi-leg block")

rows, _ = fs.rows_from_trade(PERP)
ok(rows[0]["PRODUCT"] == "DOGE PERPETUAL - PRDX", f"a perp leg's PRODUCT kind [{rows[0]['PRODUCT']}]")

_bad = json.loads(json.dumps(PSPD))
_bad["side"] = "SELL"
_bad["legs"][1]["side"] = "BUY"
ok(not fs.rows_from_trade(_bad)[1]["check"], "legs that do not net to the price fail the check")

# --- the injected payload, in each shape it arrives in --------------------
core = "r_3KGXRbs7FOrCIqOmAimxVv9dIKG"
for label, obj in (("one trade", ETH_PUT), ("a list", [PSPD, ETH_PUT]),
                   ("an API page", {"count": 2, "results": [PSPD, ETH_PUT]})):
    got = fs.from_injected(obj, core)
    ok(got is not None and got[0][0]["RFQ_ID"] == ETH_PUT["rfq_id"], f"found in {label}")
ok(fs.from_injected([PSPD], core) is None, "a payload without the RFQ is None, not a guess")
_unfilled = dict(ETH_PUT, state="REJECTED")
ok(fs.from_injected(_unfilled, core) is None, "a trade that did not fill is not a fill")
_legacy = [{"RFQ_ID": "rfq_8f3a21", "PRODUCT": "BTC OPTION - DBT", "DESCRIPTION": "Call 7 May 26 84000",
            "QTY": 50, "PRICE": 0.0122, "REF_PRICE": 0.0118, "SIDE": "BUY"}]
_got = fs.from_injected(_legacy, "rfq_8f3a21")
ok(_got is not None and _got[1] is None and _got[0][0]["_DESC_N"] == "CALL7MAY2684000",
   "the tape's uppercase rows pass through, with no package")

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "in.json"
    path.write_text("DATA: " + json.dumps(ETH_PUT))
    ok(fs.load_injected(str(path))["rfq_id"] == ETH_PUT["rfq_id"],
       "the terminal's `DATA:` label is accepted")

# --- the API lookup ---------------------------------------------------------
NOW = 1791199000000.0
ENV = {fs.ACCESS_VAR: "cred-paradigm-mainnet-access-AAAA", fs.SIGN_VAR: "sign-paradigm-mainnet-signing-BBBB"}

trade, why = fs.lookup_api(core, stop_before_ms=None, now_ms=NOW, env={}, fetch=None)
ok(trade is None and "no production Paradigm key enrolled" in why, f"no key, no call [{why}]")
trade, why = fs.lookup_api(core, stop_before_ms=None, now_ms=NOW,
                           env={fs.ACCESS_VAR: "plain", fs.SIGN_VAR: "plain"}, fetch=None)
ok(trade is None and "no production" in why, "values that are not placeholders are refused")


class Pages:
    def __init__(self, *pages, status=200):
        self.pages, self.calls, self.status = list(pages), [], status

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        return self.status, json.dumps(self.pages.pop(0)) if self.pages else "{}"


api = Pages({"results": [PSPD], "next": "CUR1"}, {"results": [ETH_PUT], "next": None})
trade, why = fs.lookup_api(core, stop_before_ms=None, now_ms=NOW, env=ENV, fetch=api)
ok(trade is ETH_PUT or (trade and trade["id"] == ETH_PUT["id"]), f"found on the second page [{why}]")
ok(len(api.calls) == 2 and "cursor=CUR1" in api.calls[1][0], "and the cursor was followed")
url, headers = api.calls[0]
ok(url.startswith("https://api.prod.paradigm.trade/v2/drfq/trade_tape/?page_size="),
   f"the production host and the UI's path [{url}]")
ok(headers["Authorization"] == "Bearer " + ENV[fs.ACCESS_VAR], "the access placeholder is the bearer")
ok(headers["Paradigm-API-Signature"] == ENV[fs.SIGN_VAR], "the signing placeholder is the signature")
signed = base64.b64decode(headers[fs.SIGN_HEADER]).decode()
target = url.removeprefix("https://api.prod.paradigm.trade")
ok(signed == f"{headers['Paradigm-API-Timestamp']}\nGET\n{target}\n",
   f"the signed bytes are ts, method, path-with-query and the empty body [{signed!r}]")
ok(fs.SIGN_HEADER == "X-Dime-Sign-paradigm-mainnet-signing", "the header the proxy looks for")

api = Pages({"results": [PSPD], "next": "CUR1"})
trade, why = fs.lookup_api(core, stop_before_ms=PSPD["executed_at"] + 1, now_ms=NOW, env=ENV, fetch=api)
ok(trade is None and len(api.calls) == 1, "paging stops at the tape's coverage edge")
api = Pages(status=403)
trade, why = fs.lookup_api(core, stop_before_ms=None, now_ms=NOW, env=ENV, fetch=api)
ok(trade is None and "HTTP 403" in why, f"a refusal is named [{why}]")


def boom(url, headers):
    raise OSError("tunnel failed")


trade, why = fs.lookup_api(core, stop_before_ms=None, now_ms=NOW, env=ENV, fetch=boom)
ok(trade is None and "unreachable" in why, "a network failure is a reason, not a crash")

# --- collect(): the order, and recurrence across sources ------------------


def tape_row(**over):
    row = {"product": "ETH OPTION - PRDX", "description": "Put  30 Oct 26  2450",
           "quantity": 1, "trade_price": 35.48, "mark_price": 37.08, "taker_side": "SELL",
           "asset": "ETH", "row_type": "paradigm_trade", "instrument_name": "ETH-30OCT26-2450-P",
           "rfq_id": "DRFQv2-r_older", "trade_id": "t1", "block_trade_id": "b_older"}
    row.update(over)
    return row


def run(rows, rfq=core, injected=None, lookup=None, tape_error=None, **reader):
    real = ca.read_executions

    def reader_stub(*a, **k):
        if tape_error:
            raise tape_error
        return {"rows": rows, **reader}
    ca.read_executions = reader_stub
    try:
        with tempfile.TemporaryDirectory() as d:
            counts = ca.collect(rfq, Path(d), injected=injected,
                                lookup=lookup or (lambda *a, **k: (None, "not searched")))
            out = {n: list(csv.DictReader((Path(d) / f"{n}.csv").open()))
                   if (Path(d) / f"{n}.csv").exists() else [] for n in ("fill", "hist")}
            pkg = Path(d) / "package.json"
            out["package"] = json.loads(pkg.read_text()) if pkg.exists() else None
            return counts, out
    finally:
        ca.read_executions = real


api_hit = lambda *a, **k: (ETH_PUT, "")  # noqa: E731
counts, out = run([tape_row()], lookup=api_hit, coverage_end_ms=1791180000000)
ok(counts["source"] == "api" and len(out["fill"]) == 1, f"the API resolves a fill the tape lacks [{counts}]")
ok(out["package"] and abs(out["package"]["ref_net"] + 37.08115655) < 1e-9, "and hands over the package mark")
ok({r["BLOCK_TRADE_ID"] for r in out["hist"]} == {"b_older", ETH_PUT["id"]},
   f"recurrence: the older tape block plus this one [{[r['BLOCK_TRADE_ID'] for r in out['hist']]}]")
ok(any("Paradigm's API" in n for n in counts["notes"]), "and says where the fill came from")

_seen = {}


def spy(c, **k):
    _seen.update(k)
    return ETH_PUT, ""


run([], lookup=spy, coverage_end_ms=1791180000000)
ok(_seen.get("stop_before_ms") == 1791180000000, "the API stops where the tape's coverage ends")

counts, out = run([tape_row(rfq_id="DRFQv2-" + core, block_trade_id="b_same_on_tape")],
                  injected=[ETH_PUT], lookup=lambda *a, **k: (_ for _ in ()).throw(AssertionError))
ok(counts["source"] == "injected", "the injected trade is used first")
ok([r["BLOCK_TRADE_ID"] for r in out["hist"]] == [ETH_PUT["id"]],
   "and its block counts once, even where the tape spells the id differently")

counts, out = run([tape_row(rfq_id="DRFQv2-" + core, block_trade_id="b_t")], injected=[PSPD])
ok(counts["source"] == "tape", "an injected payload without the RFQ falls through to the tape")

counts, out = run([], lookup=api_hit, tape_error=RuntimeError("publication stale"))
ok(counts["fill"] == 1 and any("history unavailable" in n for n in counts["notes"]),
   f"an unreadable tape no longer stops a run another source can resolve [{counts.get('notes')}]")
try:
    run([], lookup=lambda *a, **k: (None, "no production Paradigm key enrolled"),
        tape_error=RuntimeError("publication stale"))
    ok(False, "an unreadable tape with no other source must still fail")
except RuntimeError as exc:
    ok("publication stale" in str(exc) and "no production" in str(exc),
       f"and the failure names both reasons [{exc}]")

counts, _ = run([], lookup=lambda *a, **k: (None, "not among the 200 newest trades"))
ok(counts["fill"] == 0 and "200 newest" in counts["api_reason"], "a miss carries the API's reason")

_called = []
run([], rfq="GRFQ-" + core, lookup=lambda *a, **k: _called.append(1) or (None, ""))
ok(not _called, "an explicit GRFQ- id is not matched against the DRFQ tape")

# --- main(): the reasons reach the reply -----------------------------------
real_collect = ca.collect
ca.collect = lambda *a, **k: {"fill": 0, "hist": 0, "blocks": 0, "coverage_complete": False,
                              "coverage_edge": "the read covers through 2026-10-05 08:00Z only",
                              "api_reason": "no production Paradigm key enrolled"}
sys_argv, err = sys.argv, io.StringIO()
sys.argv = ["collect_analysis.py", core, "--out-dir", "/tmp/x"]
with contextlib.redirect_stderr(err):
    rc = ca.main()
sys.argv = sys_argv
ca.collect = real_collect
ok(rc == 6 and "08:00Z" in err.getvalue() and "No production Paradigm key" in err.getvalue(),
   f"exit 6 names the boundary and why the API did not help [{err.getvalue().strip()[:120]}]")

# --- analyze.py: a PRDX block is benchmarked on Paradex -------------------
ok(az.ac.paradex_symbol("ETH", "30OCT26", 2450, "P") == "ETH-USD-30OCT26-2450-P", "Paradex option name")
ok(az.ac.paradex_perp_symbol("eth") == "ETH-USD-PERP", "Paradex perp name")

SUMMARY = {"ETH-USD-30OCT26-2450-P": {"symbol": "ETH-USD-30OCT26-2450-P", "mark_price": "37.01",
                                      "mark_iv": "0.489", "delta": "-0.184", "open_interest": "4",
                                      "underlying_price": "2721.5",
                                      "greeks": {"delta": "-0.184", "gamma": "0.00076", "vega": "1.89"}},
           "ETH-USD-PERP": {"symbol": "ETH-USD-PERP", "mark_price": "2722.0"}}
BBO = {"ETH-USD-30OCT26-2450-P": {"bid": "36.56", "ask": "38.52"}}
_paradex_calls = []


def fake_paradex(path, params=None, timeout=15):
    _paradex_calls.append(path)
    if path == "markets/summary":
        return {"results": [SUMMARY[params["market"]]]}
    return BBO[path.removeprefix("bbo/")]


real_paradex, real_ticker, real_bucket = az._paradex, az.fetch_ticker, az.fetch_trades_bucket
az._paradex = fake_paradex
az.fetch_ticker = lambda s: (_ for _ in ()).throw(AssertionError(f"Deribit called for {s}"))
az.fetch_trades_bucket = lambda s, n: (_ for _ in ()).throw(AssertionError(f"Deribit trades for {s}"))
try:
    _, tk = az.fetch_paradex_ticker("ETH-USD-30OCT26-2450-P")
    ok(tk["iv"] == 48.9, f"Paradex's decimal mark_iv is printed in vol points [{tk['iv']}]")
    ok(tk["bid"] == 36.56 and tk["ask"] == 38.52, "bid/ask come from bbo/ when the summary lacks them")
    ok(tk["theta"] is None and tk["vega"] == 1.89, "an unpublished greek is None, not 0")

    counts, _ = None, None
    real = ca.read_executions
    ca.read_executions = lambda *a, **k: {"rows": [tape_row()]}
    with tempfile.TemporaryDirectory() as d:
        ca.collect(core, Path(d), lookup=api_hit)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            az._run(argparse.Namespace(csv_dir=d, now_ms=int(NOW), render=True))
        block = buf.getvalue()
    ca.read_executions = real
    ok("| Seller |" in block and "Recd 36.43" in block, f"the taker SOLD the put [{block.splitlines()[0]}]")
    ok("-1.8% below mark" in block or "−1.8% below mark" in block or "1.75" in block,
       f"the offset is against the package mark [{block.splitlines()[0]}]")
    ok("Θ" not in block.split("| Greeks |")[1].split("\n")[0],
       "no theta is printed when Paradex did not publish one")
    ok("Deribit leg blocks" not in block, "a PRDX block makes no Deribit claim")
    ok("36.56/38.52" in block and "48.9v" in block, "the Live and Fair rows are Paradex's")
    ok("Spot 2,722" in block, "spot is the Paradex perp")
    ok("2 same-structure block(s)" in block, "recurrence counts the tape's older block and this one")
finally:
    az._paradex, az.fetch_ticker, az.fetch_trades_bucket = real_paradex, real_ticker, real_bucket

# --- analyze.sh hands the attached trade to collect ------------------------
SH = skillpath.scripts("paradigm-block-analyst") / "analyze.sh"


def sh_with_stdin(args, stdin):
    with tempfile.TemporaryDirectory() as bin_dir:
        stub = Path(bin_dir) / "uv"
        out = Path(bin_dir) / "argv.txt"
        copy = Path(bin_dir) / "payload.txt"
        stub.write_text(
            "#!/bin/sh\n"
            f"for a in \"$@\"; do printf '%s\\n' \"$a\"; done >> {shlex.quote(str(out))}\n"
            f"echo --- >> {shlex.quote(str(out))}\n"
            "prev=''\n"
            "for a in \"$@\"; do\n"
            f"  if [ \"$prev\" = --fill-json ]; then cat \"$a\" > {shlex.quote(str(copy))}; fi\n"
            "  prev=\"$a\"\n"
            "done\n"
            "exit 5\n")
        stub.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        subprocess.run(["bash", str(SH), *args], input=stdin, capture_output=True,
                       text=True, env=env)
        argv = out.read_text().split("---\n")[0].splitlines() if out.exists() else []
        return argv, copy.read_text() if copy.exists() else None


argv, payload = sh_with_stdin([core, "Put 30 Oct 26 2450", "--fill-json", "-"], json.dumps(ETH_PUT))
ok("--fill-json" in argv, f"analyze.sh passes --fill-json on [{argv}]")
ok(payload is not None and json.loads(payload)["id"] == ETH_PUT["id"],
   "and the stdin payload reaches collect intact")
ok(argv[:2] == ["run", argv[1]] and core in argv and "Put 30 Oct 26 2450" not in argv,
   "the free-text description still never reaches collect")
argv, _ = sh_with_stdin([core], "")
ok("--fill-json" not in argv, "without the flag nothing extra is passed")

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
