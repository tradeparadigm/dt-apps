#!/usr/bin/env python3
"""
Unit tests for interest.py, the "why this matters to you" line — no network.
Run: python3 tests/test_interest.py

The line comes from JEV through the sidecar's loopback relay: the taker's bet,
from the block alone, and what the block is to the user, from their own
Paradigm history. These pin what the line says for each answer, that silence is
the answer whenever something is missing or unsure, what JEV is sent, and where
analyze.sh puts the line.
"""
import datetime as dt
import http.server
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))
import skillpath  # noqa: E402
sys.path.insert(0, str(skillpath.scripts("paradigm-block-analyst")))
import interest  # noqa: E402

_p = _f = 0


def ok(cond, msg):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  {msg}")
    else:
        _f += 1
        print(f"FAIL  {msg}")


# --- what each answer says ---------------------------------------------------

def resp(score=None, conf=None, choice=None, p_open=None, price=None, vol=None, p=0.9):
    a = {}
    if price is not None:
        a["trade_price_bet"] = {"choice": price, "probabilities": {price: p}}
    if vol is not None:
        a["trade_vol_bet"] = {"choice": vol, "probabilities": {vol: p}}
    if score is not None:
        a["your_kind_of_trade"] = {"score": score, "confidence": conf}
    if choice is not None:
        a["still_holds_it"] = {"choice": choice, "probabilities": {"still_open": p_open}}
    return {"answers": a}


# The playground runs that set the wording (v4): 2.87, 2.03, 1.04, 0.03.
ok("Your kind of trade" in (interest.line_for(resp(2.87, 0.88)) or ""), "2.87 at 88% is their kind of trade")
ok("outside your usual" in (interest.line_for(resp(2.03, 0.97)) or ""), "2.03 is their structure outside their range")
ok(interest.line_for(resp(1.04, 0.95)) is None, "1.04 (coin only) says nothing")
ok(interest.line_for(resp(0.03, 0.95)) is None, "0.03 (another coin) says nothing")
ok(interest.line_for(resp(2.9, 0.4)) is None, "an unsure score says nothing")
ok(interest.line_for(resp(2.9)) is None, "a score with no confidence says nothing")
ok("hasn't closed it" in (interest.line_for(resp(2.9, 0.9, "still_open", 0.85)) or ""),
   "an open position outranks the kind of trade")
ok("Your kind of trade" in (interest.line_for(resp(2.9, 0.9, "still_open", 0.6)) or ""),
   "an unsure open position falls back to the kind of trade")
ok(interest.line_for(resp(choice="closed", p_open=0.05)) is None, "closed with no score says nothing")
ok("Your kind" in (interest.line_for({"your_kind_of_trade": {"score": 3, "confidence": 0.9}}) or ""),
   "answers at the top level are read too")

# The taker's bet, which needs no history.
ok(interest.line_for(resp(price="down", vol="rises"), "ETH") == "*The taker is betting ETH falls and volatility rises.*",
   "a put bought: ETH falls and volatility rises")
ok(interest.line_for(resp(price="neither", vol="falls"), "BTC") == "*The taker is betting volatility falls.*",
   "no price direction: only the volatility half")
ok(interest.line_for(resp(price="up", vol="neither"), None) == "*The taker is betting the price rises.*",
   "no coin known: 'the price'")
ok(interest.line_for(resp(price="up", vol="rises", p=0.5), "BTC") is None, "an unsure bet says nothing")
ok(interest.line_for(resp(2.87, 0.88, price="up", vol="falls"), "BTC")
   == "*The taker is betting BTC rises and volatility falls.* *Your kind of trade: you've traded this "
      "structure on this coin at similar strikes and expiries.*", "the bet comes first, then the history")

# A vertical spread's direction is worked out, not asked (JEV split 50/50 on them).
SPB = interest.spread_price_bet
ok(SPB([("BUY", "BTC-27NOV26-120000-C"), ("SELL", "BTC-27NOV26-130000-C")]) == "up", "call spread bought: up")
ok(SPB([("SELL", "BTC-27NOV26-120000-C"), ("BUY", "BTC-27NOV26-130000-C")]) == "down", "call spread sold: down")
ok(SPB([("SELL", "ETH-27NOV26-2200-P"), ("BUY", "ETH-27NOV26-2000-P")]) == "up", "put spread sold: up")
ok(SPB([("BUY", "ETH-27NOV26-2200-P"), ("SELL", "ETH-27NOV26-2000-P")]) == "down", "put spread bought: down")
ok(SPB([("BUY", "ETH-27NOV26-2200-P"), ("BUY", "ETH-27NOV26-2200-C")]) is None, "a straddle is not a spread")
ok(SPB([("SELL", "BTC-27NOV26-110000-P"), ("BUY", "BTC-27NOV26-130000-C")]) is None, "a risk reversal is not a spread")
ok(SPB([("BUY", "ETH-27NOV26-2200-P"), ("SELL", "ETH-25DEC26-2200-P")]) is None, "a calendar is not a vertical spread")
ok(interest.line_for(resp(vol="neither"), "ETH", "up") == "*The taker is betting ETH rises.*",
   "a spread's worked-out direction stands in for JEV's")

# The shape JEV answered through LiteLLM on testnet (jev-1.13.0), names swapped in.
REAL = {"model": "jev-1.13.0", "answers": {
    "your_kind_of_trade": {"type": "score", "score": 2.92, "confidence": 0.9,
                           "legend": {"0": "a", "1": "b", "2": "c", "3": "d"},
                           "probabilities": {"0": 0.0, "1": 0.02, "2": 0.04, "3": 0.94}},
    "still_holds_it": {"type": "choice", "choice": "closed", "confidence": 0.95,
                       "probabilities": {"still_open": 0.03, "closed": 0.95, "never": 0.02}}},
    "usage": {"input_tokens": 361, "output_tokens": 44}}
ok("Your kind of trade" in (interest.line_for(REAL) or ""), "the real response shape is read")
REAL["answers"]["still_holds_it"].update(choice="still_open", probabilities={"still_open": 0.9})
ok("hasn't closed it" in (interest.line_for(REAL) or ""), "and its choice answer too")

# --- what history JEV gets ---------------------------------------------------
NOW = dt.datetime(2026, 10, 7, 9, 0, tzinfo=dt.timezone.utc)
trades = [
    {"rfq_id": "r_THIS", "description": "BTC 30OCT26 110000/130000 RR", "side": "BUY",
     "quantity": "250", "price": "-0.001", "created_at": "2026-10-07T08:00:00Z"},
    {"rfq_id": "r_A", "description": "BTC 30OCT26 110000/130000 RR", "side": "BUY",
     "quantity": "100", "price": "-0.0035", "created_at": 1790085791000},
    {"rfq_id": "r_B", "legs": [{"instrument_name": "BTC-25SEP26-105000-P", "side": "BUY"}],
     "side": "BUY", "quantity": 50, "price": 0.0102, "created_at": "2026-08-14T16:20:02Z"},
    {"rfq_id": "r_OLD", "description": "BTC 26JUN26 90000 Put", "side": "SELL",
     "quantity": 10, "price": 0.01, "created_at": "2026-05-01T00:00:00Z"},
    {"rfq_id": "r_NODESC", "side": "BUY", "quantity": 1, "created_at": "2026-10-01T00:00:00Z"},
    {"rfq_id": "r_C", "description": "Put 23 Oct 26 2200", "strategy_description": "XB_ETH-23OCT26-2200-P",
     "side": "SELL", "quantity": 20, "created_at": "2026-09-30T00:00:00Z"},
    {"rfq_id": "r_SPD", "description": "PSpd 27 Nov 26 2200/2000", "side": "SELL", "quantity": "50",
     "executed_at": 1791000000000, "legs": [
         {"instrument_name": "ETH-27NOV26-2200-P", "side": "BUY", "quantity": "50"},
         {"instrument_name": "ETH-27NOV26-2000-P", "side": "SELL", "quantity": "50"}]},
    {"rfq_id": "r_D", "description": "Call 30 Oct 26 4000", "base_currency": "eth",
     "side": "BUY", "quantity": 5, "created_at": "2026-09-29T00:00:00Z"},
]
rows = interest.history_rows(trades, "r_THIS", NOW)
descs = [r["description"] for r in rows]
ok("r_THIS" not in json.dumps(rows) and len(rows) == 5, f"the analysed block, old and unreadable rows are left out [{descs}]")
ok(rows[0]["date"] == "2026-09-22" and rows[0]["side"] == "BUY", f"an epoch-ms time becomes a date [{rows[0]}]")
ok(descs[1] == "BTC bought BTC-25SEP26-105000-P", f"a row without a description is described by its legs [{descs[1]}]")
ok(descs[0] == "BTC 30OCT26 110000/130000 RR", f"a description that leads with its coin is kept [{descs[0]}]")
ok(descs[2] == "ETH Put 23 Oct 26 2200 (XB_ETH-23OCT26-2200-P)",
   f"a coinless description gets the coin and instrument of its strategy [{descs[2]}]")
ok(descs[3] == "ETH PSpd 27 Nov 26 2200/2000: sold ETH-27NOV26-2200-P x50; bought ETH-27NOV26-2000-P x50",
   f"a sold package's legs are held flipped [{descs[3]}]")
ok(descs[4] == "ETH Call 30 Oct 26 4000", f"or the coin of base_currency [{descs[4]}]")
ok(interest.coin_of("BTC-PERPETUAL") == "BTC" and interest.coin_of("Put 23 Oct 26 2200") is None,
   "a perpetual names its coin; a bare description names none")
ok(len(interest.history_rows([dict(trades[1], rfq_id=f"r_{i}") for i in range(500)], "", NOW))
   == interest.HISTORY_ROWS, "history is capped")

# Paradigm's documented GET /v2/drfq/trades response (two of its results, as
# published), plus a rejected trade: the shape history_rows has to read.
DOC = [
    {"id": "bt_2IbpRmMSOqnQKwPkGEDr2VDZp5e", "rfq_id": "r_2IbpMsUESAt5bfVEQp7c32SbIDJ", "venue": "DBT",
     "kind": "OPTION", "state": "COMPLETED", "role": "TAKER", "executed_at": 1670460131896.015,
     "side": "BUY", "price": "0.2397", "quantity": "50",
     "legs": [{"instrument_name": "BTC-30JUN23-14000-P", "price": "0.2397", "quantity": "50", "ratio": "1",
               "side": "BUY"}],
     "strategy_description": "DO_BTC-30JUN23-14000-P", "description": "Put  30 Jun 23  14000"},
    {"id": "bt_2IbpBwZLazIBOcI24DYsD7ihRoy", "rfq_id": "r_2Ibp7AUyp9HTimRTY1TiGqNcI9a", "venue": "DBT",
     "kind": "OPTION", "state": "COMPLETED", "role": "MAKER", "executed_at": 1670460005229.808,
     "side": "SELL", "price": "4.4713", "quantity": "50",
     "legs": [{"instrument_name": "BTC-30JUN23-100000-P", "quantity": "50", "ratio": "1", "side": "BUY"},
              {"instrument_name": "BTC-30JUN23-15000-P", "quantity": "100", "ratio": "2", "side": "SELL"}],
     "strategy_description": "DO_BTC-30JUN23-100000-P_BTC-30JUN23-15000-P",
     "description": "Cstm  +1  Put  30 Jun 23  100000\n      -2  Put  30 Jun 23  15000"},
    {"id": "bt_rej", "rfq_id": "r_rej", "state": "REJECTED", "executed_at": 1670460000000.0, "side": "BUY",
     "quantity": "5", "legs": [{"instrument_name": "BTC-30JUN23-20000-P", "quantity": "5", "side": "BUY"}],
     "description": "Put  30 Jun 23  20000"},
]
doc_rows = interest.history_rows(DOC, "", dt.datetime(2022, 12, 9, tzinfo=dt.timezone.utc))
doc_descs = [r["description"] for r in doc_rows]
ok(len(doc_rows) == 2 and "20000" not in json.dumps(doc_rows), f"a rejected trade is not history [{doc_descs}]")
ok(doc_rows[0] == {"date": "2022-12-08", "description": "BTC Put 30 Jun 23 14000: bought BTC-30JUN23-14000-P x50",
                   "side": "BUY", "quantity": "50", "price": "0.2397"}, f"the documented row reads [{doc_rows[0]}]")
ok(doc_descs[1] == "BTC Cstm +1 Put 30 Jun 23 100000 -2 Put 30 Jun 23 15000: "
                   "sold BTC-30JUN23-100000-P x50; bought BTC-30JUN23-15000-P x100",
   f"a maker's SELL of a package holds its legs flipped, on one line [{doc_descs[1]}]")

# --- which credentials --------------------------------------------------------
saved = dict(os.environ)
for k in [k for k in os.environ if k.startswith("CRED_") or k in ("PARADIGM_SIGN", "PARADIGM_ACCESS", "HOST")]:
    del os.environ[k]
os.environ.update({"CRED_PARADIGM_MAINNET_SIGNING": "sign-x", "CRED_PARADIGM_MAINNET_ACCESS": "cred-y"})
ok(interest.paradigm_credentials() == ("CRED_PARADIGM_MAINNET_ACCESS", "CRED_PARADIGM_MAINNET_SIGNING",
                                       "api.prod.paradigm.trade"), "one mainnet pair is found")
os.environ["CRED_PARADIGM_TESTNET_SIGNING"] = "sign-z"
ok(interest.paradigm_credentials() is None, "two signing keys are ambiguous: nothing is guessed")
del os.environ["CRED_PARADIGM_TESTNET_SIGNING"]
del os.environ["CRED_PARADIGM_MAINNET_ACCESS"]
ok(interest.paradigm_credentials() is None, "a signing key without its access key is not used")
os.environ.clear()
os.environ.update(saved)

# --- end to end against a fake relay -----------------------------------------
class Relay(http.server.BaseHTTPRequestHandler):
    caps = ["jev"]
    jev_status = 200
    answer = resp(2.87, 0.88)
    asked = []

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(200, {"ok": True, "capabilities": Relay.caps})

    def do_POST(self):
        Relay.asked.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self._send(Relay.jev_status, Relay.answer)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Relay)
threading.Thread(target=srv.serve_forever, daemon=True).start()
interest.SIDECAR = f"http://127.0.0.1:{srv.server_address[1]}"
interest.JEV_URL = f"http://127.0.0.1:{srv.server_address[1]}/api/jev"
interest.paradigm_credentials = lambda: ("A", "S", "h")
interest.fetch_trades = lambda creds, pages=3: trades

tmp = Path(tempfile.mkdtemp())
fill = tmp / "fill.csv"
fill.write_text("PRODUCT,DESCRIPTION,QTY,SIDE,RFQ_ID,INSTRUMENT\n"
                "BTC OPTION - DBT,30 Oct 26 110000/130000 RR,250,SELL,r_THIS,BTC-30OCT26-110000-P\n"
                "BTC OPTION - DBT,30 Oct 26 110000/130000 RR,250,BUY,r_THIS,BTC-30OCT26-130000-C\n")
eth = tmp / "eth.csv"
eth.write_text("PRODUCT,DESCRIPTION,QTY,SIDE,RFQ_ID\nETH OPTION - DBT,Put 23 Oct 26 2200,50,SELL,r_E\n")
ok(interest.block_trade(str(eth)) == ("ETH Put 23 Oct 26 2200: the taker sold it x50", "r_E", "ETH", None),
   f"no INSTRUMENT column: the coin comes from PRODUCT [{interest.block_trade(str(eth))}]")


def run_main(*extra):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = interest.main(["--fill-csv", str(fill), *extra])
    return code, buf.getvalue().strip()


code, out = run_main()
sent = Relay.asked[-1]
ok(code == 0 and "Your kind of trade" in out, f"the relay's answer becomes the line [{out}]")
ok(set(sent["questions"]) == {"trade_price_bet", "trade_vol_bet", "your_kind_of_trade", "still_holds_it"}
   and sent["state"]["trade"]
   == "BTC 30 Oct 26 110000/130000 RR: the taker sold BTC-30OCT26-110000-P x250; bought BTC-30OCT26-130000-C x250",
   f"JEV is asked every question about the block, coin and instruments named [{sent['state']['trade']}]")
ok("r_THIS" not in sent["state"]["trade_history"] and "r_A" not in sent["state"]["trade_history"]
   and "110000/130000" in sent["state"]["trade_history"], "the history sent is the slim rows, without the block itself")

Relay.caps = ["run-activity"]
n = len(Relay.asked)
code, out = run_main()
ok(code == 0 and out == "" and len(Relay.asked) == n, "an agent without the relay is not asked and says nothing")
Relay.caps = ["jev"]
Relay.jev_status, Relay.answer = 429, {"error": "Budget has been exceeded"}
code, out = run_main()
ok(code == 0 and out == "", "a key without credit says nothing")
Relay.jev_status, Relay.answer = 200, resp(2.87, 0.88)
spread = tmp / "spread.csv"
spread.write_text("PRODUCT,DESCRIPTION,QTY,SIDE,RFQ_ID,INSTRUMENT\n"
                  "ETH OPTION - DBT,PSpd 27 Nov 26 2200/2000,50,SELL,r_S,ETH-27NOV26-2200-P\n"
                  "ETH OPTION - DBT,PSpd 27 Nov 26 2200/2000,50,BUY,r_S,ETH-27NOV26-2000-P\n")
Relay.answer = resp(2.87, 0.88, vol="neither")
import contextlib, io
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    interest.main(["--fill-csv", str(spread)])
ok("trade_price_bet" not in Relay.asked[-1]["questions"] and buf.getvalue().startswith("*The taker is betting ETH rises.*"),
   f"a spread is not asked its direction; the line still says it [{buf.getvalue().strip()}]")
Relay.answer = resp(2.87, 0.88)
code, out = run_main("--print-request")
ok(code == 0 and json.loads(out)["state"]["trade"].startswith("BTC 30 Oct 26"),
   "--print-request prints the request for the playground")
Relay.answer = resp(price="up", vol="rises")
interest.fetch_trades = lambda creds, pages=3: []
code, out = run_main()
sent = Relay.asked[-1]
ok(code == 0 and out == "*The taker is betting BTC rises and volatility rises.*"
   and set(sent["questions"]) == {"trade_price_bet", "trade_vol_bet"} and "trade_history" not in sent["state"],
   f"no history: only the bet is asked, and answered [{out}]")
interest.paradigm_credentials = lambda: None
code, out = run_main()
ok(code == 0 and out.startswith("*The taker is betting") and "trade_history" not in Relay.asked[-1]["state"],
   "no Paradigm credentials: the bet still is")
srv.shutdown()

# --- where analyze.sh puts the line ------------------------------------------
SH = skillpath.scripts("paradigm-block-analyst") / "analyze.sh"


def analyze_with(render_out, render_exit, line):
    """analyze.sh with uv stubbed: collect writes a fill, the render prints
    render_out and exits render_exit, interest.py prints line."""
    with tempfile.TemporaryDirectory() as bin_dir:
        stub = Path(bin_dir) / "uv"
        stub.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *collect_analysis.py*)\n"
            "    while [ $# -gt 0 ]; do [ \"$1\" = --out-dir ] && d=\"$2\"; shift; done\n"
            "    echo 'DESCRIPTION' > \"$d/fill.csv\"; exit 0 ;;\n"
            f"  *interest.py*) printf '%s' {shlex.quote(line)}; exit 0 ;;\n"
            f"  *analyze.py*) printf '%s\\n' {shlex.quote(render_out)}; exit {render_exit} ;;\n"
            "esac\nexit 9\n")
        stub.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        r = subprocess.run(["bash", str(SH), "r_TEST"], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout


code, out = analyze_with("**BTC block**\n| Greeks | x |", 0, "*Your kind of trade.*")
ok(code == 0 and out.rstrip().endswith("| Greeks | x |\n\n*Your kind of trade.*"),
   f"exit 0: the line closes the block [{out!r}]")
code, out = analyze_with("**BTC block**\n\n## For the agent\n\ntask", 1, "*Your kind of trade.*")
ok(code == 1 and out.index("*Your kind of trade.*") < out.index("## For the agent"),
   f"exit 1: the line stays with the block, above the agent's section [{out!r}]")
code, out = analyze_with("**BTC block**", 0, "")
ok(code == 0 and out == "**BTC block**\n", f"no line: the block is unchanged [{out!r}]")

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
