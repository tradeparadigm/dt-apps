#!/usr/bin/env python3
"""
Unit tests for interest.py, the "why this matters to you" line — no network.
Run: python3 tests/test_interest.py

The line comes from JEV through the sidecar's loopback relay, over the user's own
Paradigm history. These pin what the line says for each answer, that silence is
the answer whenever something is missing or unsure, what history JEV is sent,
and where analyze.sh puts the line.
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

def resp(score=None, conf=None, choice=None, p_open=None):
    a = {}
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
ok("haven't closed it" in (interest.line_for(resp(2.9, 0.9, "still_open", 0.85)) or ""),
   "an open position outranks the kind of trade")
ok("Your kind of trade" in (interest.line_for(resp(2.9, 0.9, "still_open", 0.6)) or ""),
   "an unsure open position falls back to the kind of trade")
ok(interest.line_for(resp(choice="closed", p_open=0.05)) is None, "closed with no score says nothing")
ok("Your kind" in (interest.line_for({"your_kind_of_trade": {"score": 3, "confidence": 0.9}}) or ""),
   "answers at the top level are read too")

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
ok("haven't closed it" in (interest.line_for(REAL) or ""), "and its choice answer too")

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
]
rows = interest.history_rows(trades, "r_THIS", NOW)
descs = [r["description"] for r in rows]
ok("r_THIS" not in json.dumps(rows) and len(rows) == 2, f"the analysed block, old and unreadable rows are left out [{descs}]")
ok(rows[0]["date"] == "2026-09-22" and rows[0]["side"] == "BUY", f"an epoch-ms time becomes a date [{rows[0]}]")
ok(descs[1] == "buy BTC-25SEP26-105000-P", f"a row without a description is described by its legs [{descs[1]}]")
ok(len(interest.history_rows([dict(trades[1], rfq_id=f"r_{i}") for i in range(500)], "", NOW))
   == interest.HISTORY_ROWS, "history is capped")

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
fill.write_text("PRODUCT,DESCRIPTION,QTY,SIDE,RFQ_ID\nBTC,BTC 30OCT26 110000/130000 RR,250,BUY,r_THIS\n")


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
ok(set(sent["questions"]) == {"your_kind_of_trade", "still_holds_it"}
   and sent["state"]["trade"] == "BTC 30OCT26 110000/130000 RR x250, taker buy",
   f"JEV is asked both questions about the block [{sent['state']['trade']}]")
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
code, out = run_main("--print-request")
ok(code == 0 and json.loads(out)["state"]["trade"].startswith("BTC 30OCT26"),
   "--print-request prints the request for the playground")
interest.fetch_trades = lambda creds, pages=3: []
n = len(Relay.asked)
code, out = run_main()
ok(code == 0 and out == "" and len(Relay.asked) == n, "no history: nothing is asked")
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
