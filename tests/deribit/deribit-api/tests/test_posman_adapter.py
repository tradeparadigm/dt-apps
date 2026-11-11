"""The posman adapter for Deribit (posman/account/fetch.mjs), against a local Deribit.

The adapter is run as the posman runner runs it, `node fetch.mjs` with the
runner's environment, pointed at a stand-in for Deribit's HTTP API over plain
HTTP (POSMAN_DERIBIT_API_URL, the adapter's one test hook). The stand-in
answers as Deribit does: JSON-RPC over GET, every error an HTTP 400 with a
JSON-RPC error. Nothing here reaches Deribit or a credential proxy.

What is held: the one call that carries the credential (GET public/auth with
the placeholder in the query string, as the deribit-api skill prescribes and
the proxy watches), the bearer token on everything else, the raw datasets
unchanged, posman's rows, `_errors` for what failed, and the exits (3 for a
refused credential, 2 for a source set up wrong).

Standard library only: run it as `python3 test_posman_adapter.py`.
"""

import json
import os
import pathlib
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADAPTER = (
    pathlib.Path(__file__).resolve().parents[4]
    / "apps/deribit/skills/deribit-api/posman/account/fetch.mjs"
)

CRED = "CRED_DERIBIT_TESTNET_SECRET"
PLACEHOLDER = "cred-deribit-testnet-secret-0123456789abcdef"
CLIENT_ID = "AbCdEfGh"
TOKEN = "1580293819.token"

# ---- what the stand-in serves ----

PERP = {
    "instrument_name": "BTC-PERPETUAL", "kind": "future", "direction": "buy",
    "size": 1000.0, "size_currency": 0.0135, "average_price": 74000.5,
    "mark_price": 74016.26, "index_price": 73992.76, "total_profit_loss": 1.23e-06,
    "floating_profit_loss": 1e-06, "realized_funding": -2e-07, "leverage": 50,
    "delta": 0.0135, "estimated_liquidation_price": None,
}
DATED = {
    "instrument_name": "BTC-26JUN26", "kind": "future", "direction": "buy",
    "size": 500.0, "size_currency": 0.0067, "average_price": 75500,
    "mark_price": 75100.5, "index_price": 73992.76, "total_profit_loss": -0.0000351,
    "leverage": 35.639422, "delta": 0.0067,
}
OPTION = {
    "instrument_name": "BTC-25DEC26-116000-C", "kind": "option", "direction": "sell",
    "size": -2.5, "average_price": 0.045, "average_price_usd": 3300.1,
    "mark_price": 0.04, "index_price": 73992.76, "total_profit_loss": 0.0125,
    "delta": -0.50765, "gamma": -0.000025, "vega": -182.73353, "theta": 253.83205,
}
LINEAR = {
    "instrument_name": "ETH_USDC-PERPETUAL", "kind": "future", "direction": "sell",
    "size": -1.5, "size_currency": -1.5, "average_price": 2500, "mark_price": 2450.5,
    "index_price": 2449.9, "total_profit_loss": 74.25, "leverage": 10, "delta": -1.5,
}
CLOSED = {
    "instrument_name": "ETH-PERPETUAL", "kind": "future", "direction": "zero",
    "size": 0.0, "size_currency": 0.0, "average_price": 0, "mark_price": 2450.1,
}
POSITIONS = [PERP, DATED, OPTION, LINEAR, CLOSED]

ORDERS = [
    {"order_id": "12345", "instrument_name": "BTC-PERPETUAL", "direction": "buy",
     "order_type": "limit", "order_state": "open", "amount": 1000.0, "filled_amount": 0.0,
     "price": 60000.0, "time_in_force": "good_til_cancelled", "post_only": True,
     "reduce_only": False, "label": "myorder1", "creation_timestamp": 1762329600000},
    {"order_id": "12346", "instrument_name": "BTC-PERPETUAL", "direction": "sell",
     "order_type": "stop_market", "order_state": "untriggered", "amount": 500.0,
     "filled_amount": 0.0, "price": "market_price", "trigger_price": 70000.0,
     "trigger": "mark_price", "time_in_force": "good_til_cancelled", "post_only": False,
     "reduce_only": True, "label": "", "creation_timestamp": 1762329601000},
    {"order_id": "12347", "instrument_name": "BTC-25DEC26-116000-C", "direction": "sell",
     "order_type": "limit", "order_state": "open", "amount": 1.5, "filled_amount": 0.5,
     "price": 0.05, "time_in_force": "good_til_cancelled", "post_only": False,
     "reduce_only": False, "label": "callsell", "creation_timestamp": 1762329602000},
    {"order_id": "12348", "instrument_name": "ETH_USDC-PERPETUAL", "direction": "buy",
     "order_type": "take_limit", "order_state": "untriggered", "amount": 2.0,
     "filled_amount": 0.0, "price": 2400.0, "trigger_price": 2420.0,
     "time_in_force": "good_til_day", "post_only": False, "reduce_only": False,
     "creation_timestamp": 1762329603000},
    {"order_id": "12349", "instrument_name": "BTC-FS-26JUN26_PERP", "direction": "buy",
     "order_type": "limit", "order_state": "open", "amount": 100.0, "filled_amount": 0.0,
     "price": 150.0, "time_in_force": "good_til_cancelled", "creation_timestamp": 1762329604000},
]

SUMMARIES = [
    {"currency": "BTC", "balance": 0.5, "equity": 0.51, "available_funds": 0.4,
     "initial_margin": 0.05, "maintenance_margin": 0.03, "delta_total": -0.49},
    {"currency": "ETH", "balance": 0.0, "equity": 0.0},
    {"currency": "USDC", "balance": 12500.42, "equity": 12574.67},
    {"currency": "USDT", "balance": -10.5, "equity": -10.5},
]

INSTRUMENTS = {
    "BTC-PERPETUAL": {
        "instrument_name": "BTC-PERPETUAL", "kind": "future", "instrument_type": "reversed",
        "settlement_period": "perpetual", "base_currency": "BTC", "quote_currency": "USD",
        "counter_currency": "USD", "settlement_currency": "BTC", "contract_size": 10.0,
        "expiration_timestamp": 32503708800000,
    },
    "BTC-26JUN26": {
        "instrument_name": "BTC-26JUN26", "kind": "future", "instrument_type": "reversed",
        "settlement_period": "month", "base_currency": "BTC", "quote_currency": "USD",
        "counter_currency": "USD", "settlement_currency": "BTC", "contract_size": 10.0,
        "expiration_timestamp": 1782460800000,
    },
    "BTC-25DEC26-116000-C": {
        "instrument_name": "BTC-25DEC26-116000-C", "kind": "option", "instrument_type": "reversed",
        "settlement_period": "month", "base_currency": "BTC", "quote_currency": "BTC",
        "counter_currency": "USD", "settlement_currency": "BTC", "contract_size": 1.0,
        "strike": 116000.0, "option_type": "call", "expiration_timestamp": 1798185600000,
    },
    "ETH_USDC-PERPETUAL": {
        "instrument_name": "ETH_USDC-PERPETUAL", "kind": "future", "instrument_type": "linear",
        "settlement_period": "perpetual", "base_currency": "ETH", "quote_currency": "USDC",
        "counter_currency": "USDC", "settlement_currency": "USDC", "contract_size": 1,
        "expiration_timestamp": 32503708800000,
    },
    "BTC-FS-26JUN26_PERP": {
        "instrument_name": "BTC-FS-26JUN26_PERP", "kind": "future_combo",
        "instrument_type": "reversed", "base_currency": "BTC", "quote_currency": "USD",
        "settlement_currency": "BTC",
    },
}

TICKERS = {
    "BTC-PERPETUAL": {"instrument_name": "BTC-PERPETUAL", "index_price": 73992.76, "funding_8h": 1.898e-05},
    "ETH_USDC-PERPETUAL": {"instrument_name": "ETH_USDC-PERPETUAL", "index_price": 2449.9, "funding_8h": -0.00008},
    "BTC-25DEC26-116000-C": {"instrument_name": "BTC-25DEC26-116000-C", "index_price": 73992.76,
                             "mark_iv": 122.21, "funding_8h": None},
}


class Deribit:
    """The stand-in's state: what it answers, and every request it saw."""

    def __init__(self):
        self.requests = []
        self.auth_error = None  # (http status, code, message), or a bare status
        self.private_errors = {}  # method -> (code, message)
        self.fail_instruments = set()
        self.fail_tickers = False


def rpc_error(code, message):
    return 400, {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "testnet": True}


def answer(state, path, query, auth):
    method = path.removeprefix("/api/v2/")
    if method == "public/auth":
        if state.auth_error:
            if isinstance(state.auth_error, int):
                return state.auth_error, {"error": "placeholder_absent"}
            return rpc_error(*state.auth_error)
        return 200, {"jsonrpc": "2.0", "result": {
            "access_token": TOKEN, "expires_in": 900, "refresh_token": "r", "token_type": "bearer",
            "scope": "account:read trade:read"}}
    if method.startswith("private/"):
        if auth != f"Bearer {TOKEN}":
            return rpc_error(13009, "unauthorized")
        if method in state.private_errors:
            return rpc_error(*state.private_errors[method])
        if method == "private/get_positions":
            return 200, {"jsonrpc": "2.0", "result": POSITIONS}
        if method == "private/get_open_orders":
            return 200, {"jsonrpc": "2.0", "result": ORDERS}
        if method == "private/get_account_summaries":
            return 200, {"jsonrpc": "2.0", "result": {"summaries": SUMMARIES}}
    if method == "public/get_instrument":
        name = query.get("instrument_name", [""])[0]
        if name in state.fail_instruments or name not in INSTRUMENTS:
            return rpc_error(13020, "not_found")
        return 200, {"jsonrpc": "2.0", "result": INSTRUMENTS[name]}
    if method == "public/ticker":
        if state.fail_tickers:
            return rpc_error(10028, "too_many_requests")
        return 200, {"jsonrpc": "2.0", "result": TICKERS[query["instrument_name"][0]]}
    return rpc_error(-32601, "Method not found")


def serve(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
            auth = self.headers.get("Authorization")
            state.requests.append({"path": url.path, "query": query, "raw_query": url.query, "auth": auth})
            status, body = answer(state, url.path, query, auth)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run(state=None, env_extra=None, meta=None, cred=CRED, hook=True):
    state = state or Deribit()
    server = serve(state)
    try:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "POSMAN_SOURCE": "deribit-main",
            "POSMAN_VENUE": "deribit",
            "POSMAN_CRED": cred,
            cred: PLACEHOLDER,
            f"{cred}_META": json.dumps({"client_id": CLIENT_ID} if meta is None else meta),
        }
        if hook:
            env["POSMAN_DERIBIT_API_URL"] = f"http://127.0.0.1:{server.server_address[1]}/api/v2"
        env.update(env_extra or {})
        proc = subprocess.run(["node", str(ADAPTER)], cwd=ADAPTER.parent, env=env,
                              capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc, out, state


passed = 0
failed = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(f"{name}{f': {detail}' if detail else ''}")


def by(rows, key, value):
    return next((r for r in rows if r.get(key) == value), None)


# ---- a whole account ----

proc, out, seen = run()
check("exits 0", proc.returncode == 0, proc.stderr)

auths = [r for r in seen.requests if r["path"] == "/api/v2/public/auth"]
check("one token exchange", len(auths) == 1, len(auths))
q = auths[0]["query"] if auths else {}
check("the exchange is client_credentials",
      q.get("grant_type") == ["client_credentials"], q)
check("the client id comes from _META, verbatim", q.get("client_id") == [CLIENT_ID], q)
check("the placeholder is the client_secret, in the query string",
      q.get("client_secret") == [PLACEHOLDER] and f"client_secret={PLACEHOLDER}" in auths[0]["raw_query"], q)
check("the exchange carries no token", auths and auths[0]["auth"] is None)
check("no refresh_token grant, ever", not any("refresh_token" in r["query"].get("grant_type", []) for r in seen.requests))

private = [r for r in seen.requests if r["path"].startswith("/api/v2/private/")]
check("three private reads", sorted(r["path"] for r in private) == [
    "/api/v2/private/get_account_summaries", "/api/v2/private/get_open_orders", "/api/v2/private/get_positions"])
check("every private read carries the bearer token", all(r["auth"] == f"Bearer {TOKEN}" for r in private))
pos_q = next((r["query"] for r in private if r["path"].endswith("get_positions")), {})
check("positions of every currency", pos_q.get("currency") == ["any"], pos_q)
check("the placeholder goes nowhere but the exchange",
      all(PLACEHOLDER not in r["raw_query"] for r in seen.requests if r["path"] != "/api/v2/public/auth"))

check("positions are Deribit's, unchanged", out["positions"] == POSITIONS)
check("open_orders are Deribit's, unchanged", out["open_orders"] == ORDERS)
check("balances are the summaries, unchanged", out["balances"] == SUMMARIES)
check("nothing failed", "_errors" not in out, out.get("_errors"))

n = out["_normalized"]
pos = n["positions"]
check("one row per open position (the closed one left out)",
      sorted(r["symbol"] for r in pos) == ["BTC-25DEC26-116000-C", "BTC-26JUN26", "BTC-PERPETUAL", "ETH_USDC-PERPETUAL"],
      [r["symbol"] for r in pos])

perp = by(pos, "symbol", "BTC-PERPETUAL")
check("an inverse perpetual", perp and perp["kind"] == "perp" and perp["contract"] == "inverse"
      and perp["underlying"] == "BTC" and perp["quote_asset"] == "USD" and perp["settle_asset"] == "BTC"
      and perp["expiry"] is None, perp)
check("its size is size_currency, in BTC", perp and perp["size"] == "0.0135" and perp["side"] == "long", perp)
check("its prices are in USD", perp and perp["entry_price"] == "74000.5" and perp["mark_price"] == "74016.26", perp)
check("its PnL is total_profit_loss, in BTC, no exponent",
      perp and perp["unrealized_pnl"] == "0.00000123" and perp["pnl_asset"] == "BTC", perp)
check("its funding is funding_8h per hour, exactly", perp and perp["funding_rate"] == "0.0000023725", perp)
check("no funding PnL since open, no liquidation level",
      perp and perp["funding_pnl"] is None and perp["liquidation_price"] is None, perp)
check("a perp's delta is its size, its other greeks 0",
      perp and perp["delta"] == "0.0135" and perp["gamma"] == "0" and perp["vega"] == "0" and perp["theta"] == "0", perp)
check("its leverage", perp and perp["leverage"] == "50", perp)

dated = by(pos, "symbol", "BTC-26JUN26")
check("a dated future has its expiry", dated and dated["kind"] == "future"
      and dated["expiry"] == "2026-06-26T08:00:00.000Z" and dated["funding_rate"] is None, dated)
check("a fractional leverage is kept", dated and dated["leverage"] == "35.639422", dated)
check("a negative PnL from an exponent", dated and dated["unrealized_pnl"] == "-0.0000351", dated)

opt = by(pos, "symbol", "BTC-25DEC26-116000-C")
check("a coin-margined option", opt and opt["kind"] == "option" and opt["contract"] == "inverse"
      and opt["option_type"] == "call" and opt["strike"] == "116000"
      and opt["expiry"] == "2026-12-25T08:00:00.000Z", opt)
check("its rows are in USD, settled in BTC",
      opt and opt["quote_asset"] == "USD" and opt["settle_asset"] == "BTC" and opt["pnl_asset"] == "BTC", opt)
check("short: negative size in BTC", opt and opt["side"] == "short" and opt["size"] == "-2.5", opt)
check("its entry is average_price_usd", opt and opt["entry_price"] == "3300.1", opt)
check("its mark is the coin mark times the index, exactly", opt and opt["mark_price"] == "2959.7104", opt)
check("its greeks are the position's", opt and opt["delta"] == "-0.50765" and opt["gamma"] == "-0.000025"
      and opt["vega"] == "-182.73353" and opt["theta"] == "253.83205", opt)
check("its IV is mark_iv as a fraction", opt and opt["iv"] == "1.2221", opt)
check("an option has no leverage", opt and opt["leverage"] is None, opt)

lin = by(pos, "symbol", "ETH_USDC-PERPETUAL")
check("a linear USDC perpetual", lin and lin["contract"] == "linear" and lin["quote_asset"] == "USDC"
      and lin["settle_asset"] == "USDC" and lin["pnl_asset"] == "USDC", lin)
check("short, in ETH", lin and lin["side"] == "short" and lin["size"] == "-1.5" and lin["delta"] == "-1.5", lin)
check("a negative funding rate per hour", lin and lin["funding_rate"] == "-0.00001", lin)

orders = n["open_orders"]
check("one row per order (the combo left out)",
      sorted(r["order_id"] for r in orders) == ["12345", "12346", "12347", "12348"], [r["order_id"] for r in orders])
o1 = by(orders, "order_id", "12345")
check("an inverse perpetual's limit order, sized in BTC at its price",
      o1 and o1["size"] == "0.0166666667" and o1["price"] == "60000" and o1["filled_size"] == "0"
      and o1["remaining_size"] == "0.0166666667", o1)
check("post_only wins as its time in force, its label is the client id",
      o1 and o1["time_in_force"] == "post_only" and o1["client_order_id"] == "myorder1"
      and o1["type"] == "limit" and o1["status"] == "open" and o1["side"] == "buy", o1)
check("its created_at", o1 and o1["created_at"] == "2025-11-05T08:00:00.000Z", o1)
o2 = by(orders, "order_id", "12346")
check("a stop-market: untriggered, sized at its trigger, no price",
      o2 and o2["type"] == "stop_market" and o2["status"] == "untriggered" and o2["price"] is None
      and o2["trigger_price"] == "70000" and o2["size"] == "0.0071428571" and o2["reduce_only"] is True
      and o2["client_order_id"] is None, o2)
o3 = by(orders, "order_id", "12347")
check("an option order: part filled, sized in BTC",
      o3 and o3["status"] == "partially_filled" and o3["size"] == "1.5" and o3["filled_size"] == "0.5"
      and o3["remaining_size"] == "1", o3)
check("its price in USD at the index", o3 and o3["price"] == "3699.638" and o3["quote_asset"] == "USD", o3)
o4 = by(orders, "order_id", "12348")
check("a take-profit limit, good for the day",
      o4 and o4["type"] == "take_profit_limit" and o4["venue_type"] == "take_limit"
      and o4["time_in_force"] == "gtd" and o4["size"] == "2" and o4["trigger_price"] == "2420", o4)

bal = n["balances"]
check("one row per currency whose balance is not 0",
      [(b["asset"], b["total"]) for b in bal] == [("BTC", "0.5"), ("USDC", "12500.42"), ("USDT", "-10.5")], bal)
check("every number is a string",
      all(not isinstance(v, (int, float)) or isinstance(v, bool) for kind in n.values() for r in kind for v in r.values()))

# ---- refusals ----

state = Deribit()
state.auth_error = (13004, "invalid_credentials")
proc, out, seen = run(state)
check("invalid_credentials at the exchange: exit 3", proc.returncode == 3, proc.returncode)
check("nothing printed, the reason on stderr", out is None and "13004" in proc.stderr, proc.stderr)
check("nothing private asked", not any(r["path"].startswith("/api/v2/private/") for r in seen.requests))

state = Deribit()
state.auth_error = 403
proc, out, _ = run(state)
check("the proxy's 403 at the exchange: exit 3", proc.returncode == 3 and "HTTP 403" in proc.stderr, proc.stderr)

state = Deribit()
state.private_errors = {m: (13009, "unauthorized") for m in
                        ("private/get_positions", "private/get_open_orders", "private/get_account_summaries")}
proc, out, _ = run(state)
check("every private read refused: exit 3", proc.returncode == 3, proc.returncode)

state = Deribit()
state.private_errors = {"private/get_open_orders": (13021, "forbidden")}
proc, out, _ = run(state)
check("one read refused (a scope the key lacks): the rest kept", proc.returncode == 0 and "positions" in out
      and "balances" in out and "open_orders" not in out, proc.stderr)
check("named in _errors as refused", out and out["_errors"].get("open_orders", "").startswith("refused: "), out and out.get("_errors"))
check("its rows left out, the others mapped", out and "open_orders" not in out["_normalized"]
      and "positions" in out["_normalized"], out and list(out["_normalized"]))

state = Deribit()
state.private_errors = {"private/get_positions": (10028, "too_many_requests")}
proc, out, _ = run(state)
check("a failed read goes in _errors", proc.returncode == 0 and "10028" in out["_errors"]["positions"], out)
check("and leaves its rows out", "positions" not in out["_normalized"] and "open_orders" in out["_normalized"])

# ---- the lookups the rows need ----

state = Deribit()
state.fail_instruments = {"BTC-25DEC26-116000-C"}
proc, out, _ = run(state)
check("an instrument it cannot find: exit 0, _errors.instruments",
      proc.returncode == 0 and "instruments" in out["_errors"], out and out.get("_errors"))
check("a kind naming it is left out (its last rows stay), balances mapped",
      "positions" not in out["_normalized"] and "open_orders" not in out["_normalized"]
      and "balances" in out["_normalized"], list(out["_normalized"]))
check("the raw datasets still stored", out["positions"] == POSITIONS and out["open_orders"] == ORDERS)

state = Deribit()
state.fail_tickers = True
proc, out, _ = run(state)
check("no tickers: _errors.tickers", proc.returncode == 0 and "tickers" in out["_errors"], out and out.get("_errors"))
pos = out["_normalized"]["positions"]
check("rows still mapped, funding and IV null",
      by(pos, "symbol", "BTC-PERPETUAL")["funding_rate"] is None and by(pos, "symbol", "BTC-25DEC26-116000-C")["iv"] is None)
check("a coin-margined option's order price null without the index",
      by(out["_normalized"]["open_orders"], "order_id", "12347")["price"] is None)

# ---- a source set up wrong ----

proc, out, _ = run(meta={})
check("no client id in _META: exit 2", proc.returncode == 2 and "client_id" in proc.stderr, proc.stderr)
proc, out, _ = run(cred="CRED_SOMETHING_ELSE", hook=False)
check("an environment it cannot tell: exit 2", proc.returncode == 2 and "ENVIRONMENT" in proc.stderr, proc.stderr)
proc, out, _ = run(hook=False, env_extra={"POSMAN_PARAM_ENVIRONMENT": "devnet"})
check("an environment the app does not have: exit 2", proc.returncode == 2, proc.stderr)
proc, out, _ = run(env_extra={"POSMAN_CRED": ""})
check("no credential: exit 2", proc.returncode == 2, proc.stderr)

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
