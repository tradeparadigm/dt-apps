"""The posman adapter for Paradex (posman/account/fetch.py), against a local Paradex.

The adapter is run as the posman runner runs it, as a script with the
runner's environment, pointed at a stand-in for Paradex's REST API over plain
HTTP (POSMAN_PARADEX_API_URL, the adapter's one test hook). Nothing here
reaches Paradex or a credential proxy.

The signing-key path is held to paradex-py itself: the hash it sends in
X-Dime-Sign-<label> must be the one paradex-py's ExternalSignerAccount with a
HeaderSigner sends for the same message, which is the client the Paradex MCP
server signs through. Needs paradex-py (and pytest).
"""

import base64
import json
import pathlib
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

SKILL = pathlib.Path(__file__).resolve().parents[4] / "apps/paradex/skills/paradex-api"
ADAPTER = SKILL / "posman/account/fetch.py"

CHAIN = "PRIVATE_SN_PARACLEAR_MAINNET"
ACCOUNT = "0x0123abc"
PUBLIC_KEY = "0x4f3c"  # as hex() writes it: the auth path is /v1/auth/<hex(public key)>

OPEN = {
    "market": "BTC-USD-PERP",
    "side": "LONG",
    "size": "0.5",
    "status": "OPEN",
    "average_entry_price": "64000.00",
    "unrealized_pnl": "498.75",
    "unrealized_funding_pnl": "-1.25",
    "realized_positional_funding_pnl": "-0.5",
    "liquidation_price": "",
    "leverage": "5",
    "last_updated_at": 1762329600000,
}
CLOSED = {"market": "ETH-USD-PERP", "side": "SHORT", "size": "0", "status": "CLOSED"}
OPEN2 = {
    "market": "SOL-USD-PERP",
    "side": "SHORT",
    "size": "-10",
    "status": "OPEN",
    "average_entry_price": "150",
    "unrealized_pnl": "20",
    "unrealized_funding_pnl": "0",
    "realized_positional_funding_pnl": "0",
    "liquidation_price": "180.5",
    "leverage": "3",
}
OPTION = {
    "market": "BTC-USD-27JUN26-70000-C",
    "side": "SHORT",
    "size": "-2",
    "status": "OPEN",
    "average_entry_price": "1200",
    "unrealized_pnl": "-300",
    "unrealized_funding_pnl": "0",
    "realized_positional_funding_pnl": "0",
}
POSITIONS = [OPEN, OPEN2, OPTION]
ORDER = {
    "id": "1",
    "client_id": "",
    "market": "BTC-USD-PERP",
    "side": "BUY",
    "type": "STOP_LOSS_LIMIT",
    "status": "OPEN",
    "size": "0.1",
    "remaining_size": "0.04",
    "price": "60000",
    "trigger_price": "61000",
    "instruction": "GTC",
    "flags": ["REDUCE_ONLY"],
    "created_at": 1762329600000,
}
BALANCE = {"token": "USDC", "size": "1000.5", "last_updated_at": 1762329600000}
SUMMARY = {"account": ACCOUNT, "account_value": "1500.2", "status": "ACTIVE"}
MARKETS = [
    {"symbol": "BTC-USD-PERP", "asset_kind": "PERP", "base_currency": "BTC", "quote_currency": "USD",
     "settlement_currency": "USDC", "expiry_at": 0, "funding_period_hours": 8, "market_kind": "cross"},
    {"symbol": "SOL-USD-PERP", "asset_kind": "PERP", "base_currency": "SOL", "quote_currency": "USD",
     "settlement_currency": "USDC", "expiry_at": 0, "funding_period_hours": 8, "market_kind": "cross"},
    {"symbol": "BTC-USD-27JUN26-70000-C", "asset_kind": "OPTION", "base_currency": "BTC", "quote_currency": "USD",
     "settlement_currency": "USDC", "expiry_at": 1782547200000, "strike_price": "70000", "option_type": "CALL",
     "market_kind": "cross"},
]
MARKET_SUMMARIES = [
    {"symbol": "BTC-USD-PERP", "mark_price": "65000.1", "funding_rate": "0.0001", "greeks": {"delta": "1"}},
    {"symbol": "SOL-USD-PERP", "mark_price": "148", "funding_rate": "-0.00008"},
    {"symbol": "BTC-USD-27JUN26-70000-C", "mark_price": "1350", "mark_iv": "0.55",
     "greeks": {"delta": "0.42", "gamma": "0.00002", "vega": "80.1", "theta": "-12.5"}},
]


class Paradex:
    """A stand-in for Paradex's REST API: answers, and records every request."""

    def __init__(self):
        self.requests = []
        self.fail = {}  # path -> status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_any(self, method):
                path, _, query = self.path.partition("?")
                outer.requests.append({"method": method, "path": path, "query": query, "headers": dict(self.headers)})
                if path in outer.fail:
                    return self.answer(outer.fail[path], {"error": "NOPE", "message": "refused"})
                if method == "GET" and path == "/v1/system/config":
                    return self.answer(200, {"starknet_chain_id": CHAIN})
                if method == "POST" and path == f"/v1/auth/{PUBLIC_KEY}":
                    return self.answer(200, {"jwt_token": "jwt-from-paradex"})
                if method == "GET" and path == "/v1/positions":
                    if query == "cursor=page2":
                        return self.answer(200, {"results": [OPEN2, OPTION], "next": None})
                    return self.answer(200, {"results": [OPEN, CLOSED], "next": "page2"})
                if method == "GET" and path == "/v1/markets":
                    return self.answer(200, {"results": MARKETS})
                if method == "GET" and path == "/v1/markets/summary" and query == "market=ALL":
                    return self.answer(200, {"results": MARKET_SUMMARIES})
                if method == "GET" and path == "/v1/orders":
                    return self.answer(200, {"results": [ORDER]})
                if method == "GET" and path == "/v1/balance":
                    return self.answer(200, {"results": [BALANCE]})
                if method == "GET" and path == "/v1/account":
                    return self.answer(200, SUMMARY)
                return self.answer(404, {"error": "NOT_FOUND"})

            def do_GET(self):
                self.handle_any("GET")

            def do_POST(self):
                self.handle_any("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()

    def paths(self):
        return [(r["method"], r["path"]) for r in self.requests]


@pytest.fixture()
def paradex():
    p = Paradex()
    yield p
    p.close()


def run(env):
    res = subprocess.run([sys.executable, str(ADAPTER)], env=env, capture_output=True, text=True, timeout=60)
    out = json.loads(res.stdout) if res.returncode == 0 else None
    return res.returncode, out, res.stderr


def jwt_env(paradex, **extra):
    return {
        "PATH": "/usr/bin:/bin",
        "POSMAN_CRED": "CRED_PARADEX_MAINNET_JWT",
        "CRED_PARADEX_MAINNET_JWT": "cred-paradex-mainnet-jwt-0123456789",
        "CRED_PARADEX_MAINNET_JWT_META": json.dumps({"account": ACCOUNT}),
        "POSMAN_PARADEX_API_URL": paradex.url,
        **extra,
    }


def sign_env(paradex, **extra):
    return {
        "PATH": "/usr/bin:/bin",
        "POSMAN_CRED": "CRED_PARADEX_MAINNET_PRIV_KEY",
        "CRED_PARADEX_MAINNET_PRIV_KEY": "sign-paradex-mainnet-priv-key-7Kj2mQx9",
        "CRED_PARADEX_MAINNET_PRIV_KEY_META": json.dumps({"account": ACCOUNT, "public_key": PUBLIC_KEY}),
        "POSMAN_PARADEX_API_URL": paradex.url,
        **extra,
    }


def test_read_only_token_reads_every_dataset_with_the_placeholder(paradex):
    code, out, err = run(jwt_env(paradex))
    assert code == 0, err
    rows = out.pop("_normalized")
    assert out == {
        "positions": POSITIONS,
        "open_orders": [ORDER],
        "balances": [BALANCE],
        "account": SUMMARY,
    }
    assert set(rows) == {"positions", "open_orders", "balances"}
    # No auth call: the proxy swaps the token in.
    assert ("POST", f"/v1/auth/{PUBLIC_KEY}") not in paradex.paths()
    account_reads = [r for r in paradex.requests if r["method"] == "GET" and not r["path"].startswith("/v1/markets")]
    assert {r["headers"]["Authorization"] for r in account_reads} == {"Bearer cred-paradex-mainnet-jwt-0123456789"}
    # The markets are public: read with no credential at all.
    markets = [r for r in paradex.requests if r["path"].startswith("/v1/markets")]
    assert [r["path"] for r in markets] == ["/v1/markets", "/v1/markets/summary"]
    assert all("Authorization" not in r["headers"] for r in markets)
    reads = [r for r in paradex.requests if r["method"] == "GET"]
    # Every page of positions, closed ones left out.
    assert [r["query"] for r in reads if r["path"] == "/v1/positions"] == ["", "cursor=page2"]


def test_signing_key_gets_a_jwt_through_the_proxy_as_paradex_py_does(paradex):
    from paradex_py.account import ExternalSignerAccount, HeaderSigner

    code, out, err = run(sign_env(paradex))
    assert code == 0, err
    assert out["positions"] == POSITIONS
    auth = next(r for r in paradex.requests if r["path"] == f"/v1/auth/{PUBLIC_KEY}")
    h = auth["headers"]
    assert h["PARADEX-STARKNET-SIGNATURE"] == "sign-paradex-mainnet-priv-key-7Kj2mQx9"
    assert h["PARADEX-STARKNET-ACCOUNT"] == hex(int(ACCOUNT, 16))
    assert int(h["PARADEX-SIGNATURE-EXPIRATION"]) - int(h["PARADEX-TIMESTAMP"]) == 24 * 60 * 60

    # paradex-py's own signing-proxy client, for the same message.
    account = ExternalSignerAccount(
        SimpleNamespace(starknet_chain_id=CHAIN),
        ACCOUNT,
        PUBLIC_KEY,
        HeaderSigner(header="X-Dime-Sign-paradex-mainnet-priv-key", signature="sign-paradex-mainnet-priv-key-7Kj2mQx9"),
    )
    signature = account.auth_signature(int(h["PARADEX-TIMESTAMP"]), int(h["PARADEX-SIGNATURE-EXPIRATION"]))
    expected = account.take_request_headers()
    assert signature == h["PARADEX-STARKNET-SIGNATURE"]
    assert h["X-Dime-Sign-paradex-mainnet-priv-key"] == expected["X-Dime-Sign-paradex-mainnet-priv-key"]
    assert len(base64.b64decode(h["X-Dime-Sign-paradex-mainnet-priv-key"])) == 32

    # Then the reads carry the JWT Paradex answered.
    reads = [
        r
        for r in paradex.requests
        if r["method"] == "GET" and r["path"] != "/v1/system/config" and not r["path"].startswith("/v1/markets")
    ]
    assert {r["headers"]["Authorization"] for r in reads} == {"Bearer jwt-from-paradex"}


def test_a_refused_credential_is_exit_3(paradex):
    paradex.fail[f"/v1/auth/{PUBLIC_KEY}"] = 401
    code, _, err = run(sign_env(paradex))
    assert code == 3
    assert "HTTP 401" in err

    paradex.fail = {"/v1/balance": 403}
    code, _, err = run(jwt_env(paradex))
    assert code == 3
    assert "/v1/balance: HTTP 403" in err


def test_a_dataset_that_fails_is_named_and_the_rest_kept(paradex):
    paradex.fail["/v1/orders"] = 502
    code, out, err = run(jwt_env(paradex))
    assert code == 0, err
    assert "open_orders" not in out
    assert out["_errors"]["open_orders"].startswith("GET /v1/orders: HTTP 502")
    assert out["positions"] == POSITIONS and out["account"] == SUMMARY
    # Its rows are left out too, so posman keeps the last ones.
    assert set(out["_normalized"]) == {"positions", "balances"}


def test_a_source_set_up_wrong_is_exit_2(paradex):
    env = jwt_env(paradex)
    del env["POSMAN_CRED"]
    code, _, err = run(env)
    assert (code, "no credential" in err) == (2, True)

    # A label that names no environment needs the parameter.
    env = jwt_env(paradex, POSMAN_CRED="CRED_MY_PARADEX", CRED_MY_PARADEX="cred-x")
    del env["POSMAN_PARADEX_API_URL"]
    code, _, err = run(env)
    assert (code, "--param ENVIRONMENT" in err) == (2, True)

    env = sign_env(paradex)
    env["CRED_PARADEX_MAINNET_PRIV_KEY_META"] = json.dumps({"account": ACCOUNT})
    code, _, err = run(env)
    assert (code, "no account address or public key" in err) == (2, True)


def test_the_environment_comes_from_the_label_or_the_parameter():
    # No __pycache__ beside the adapter: everything under apps/ is published to a pod.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ADAPTER.parent))
    try:
        import fetch
    finally:
        sys.path.pop(0)
    assert fetch.base_url({}, "paradex-testnet-jwt") == "https://api.testnet.paradex.trade/v1"
    assert fetch.base_url({}, "paradex-mainnet-priv-key") == "https://api.prod.paradex.trade/v1"
    assert fetch.base_url({"POSMAN_PARAM_ENVIRONMENT": "nightly"}, "my-key") == "https://api.nightly.paradex.trade/v1"


def test_the_manifest_reads_as_posman_reads_it():
    text = (ADAPTER.parent / "POSMAN.md").read_text()
    assert text.startswith("---\n")
    front = dict(line.split(": ", 1) for line in text.split("---\n")[1].strip().splitlines())
    assert front["name"] == ADAPTER.parent.name == "account"
    assert front["title"] and len(front["title"]) <= 80
    assert front["credential"] == "paradex-*"
    assert front["params"] == "ENVIRONMENT?"


# What posman's runner refuses (posman's lib/normalized.mjs, SCHEMA.md).
DECIMAL = re.compile(r"^-?\d{1,40}(\.\d{1,40})?$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def assert_schema_shapes(row):
    for key, value in row.items():
        if value is None or isinstance(value, bool):
            continue
        assert isinstance(value, str), (key, value)
        if key.endswith("_at") or key == "expiry":
            assert TIME.match(value), (key, value)


def test_the_rows_follow_posmans_schema(paradex):
    code, out, err = run(jwt_env(paradex))
    assert code == 0, err
    rows = out["_normalized"]
    btc, sol, option = rows["positions"]
    assert btc == {
        "symbol": "BTC-USD-PERP",
        "kind": "perp",
        "underlying": "BTC",
        "quote_asset": "USD",
        "settle_asset": "USDC",
        "contract": "linear",
        "expiry": None,
        "strike": None,
        "option_type": None,
        "side": "long",
        "size": "0.5",
        "entry_price": "64000",
        "mark_price": "65000.1",
        # No level: null, never "".
        "liquidation_price": None,
        # Paradex's 498.75 includes -1.25 of running funding; posman's excludes it.
        "unrealized_pnl": "500",
        # Since the position opened: realized -0.5 plus running -1.25.
        "funding_pnl": "-1.75",
        "pnl_asset": "USDC",
        "margin_mode": "cross",
        "leverage": "5",
        # 0.01% per 8 hours, per hour.
        "funding_rate": "0.0000125",
        "delta": "0.5",
        "gamma": "0",
        "vega": "0",
        "theta": "0",
        "iv": None,
        "updated_at": "2025-11-05T08:00:00.000Z",
    }
    assert (sol["side"], sol["size"], sol["liquidation_price"], sol["delta"], sol["funding_rate"]) == (
        "short",
        "-10",
        "180.5",
        "-10",
        "-0.00001",
    )
    # An option: its strike and expiry from the market, greeks per unit times the signed size.
    assert {k: option[k] for k in ("kind", "strike", "option_type", "expiry", "delta", "gamma", "vega", "theta", "iv")} == {
        "kind": "option",
        "strike": "70000",
        "option_type": "call",
        "expiry": "2026-06-27T08:00:00.000Z",
        "delta": "-0.84",
        "gamma": "-0.00004",
        "vega": "-160.2",
        "theta": "25",
        "iv": "0.55",
    }
    assert option["funding_rate"] is None, "a dated option pays no funding"

    (order,) = rows["open_orders"]
    assert {k: order[k] for k in order if k not in ("symbol", "kind", "underlying", "quote_asset", "settle_asset",
                                                   "contract", "expiry", "strike", "option_type")} == {
        "order_id": "1",
        "client_order_id": None,
        "side": "buy",
        "type": "stop_limit",
        "venue_type": "STOP_LOSS_LIMIT",
        "status": "partially_filled",
        "venue_status": "OPEN",
        "price": "60000",
        "trigger_price": "61000",
        "size": "0.1",
        "filled_size": "0.06",
        "remaining_size": "0.04",
        "time_in_force": "gtc",
        "reduce_only": True,
        "created_at": "2025-11-05T08:00:00.000Z",
    }
    assert rows["balances"] == [{"asset": "USDC", "total": "1000.5", "updated_at": "2025-11-05T08:00:00.000Z"}]

    for kind in rows.values():
        for row in kind:
            assert_schema_shapes(row)
    for row in rows["positions"]:
        assert DECIMAL.match(row["size"]) and (row["size"].startswith("-")) == (row["side"] == "short")


def test_without_the_markets_list_positions_and_orders_keep_their_last_rows(paradex):
    paradex.fail["/v1/markets"] = 502
    code, out, err = run(jwt_env(paradex))
    assert code == 0, err
    assert set(out["_normalized"]) == {"balances"}
    assert out["_errors"]["markets"].startswith("GET /v1/markets: GET /v1/markets: HTTP 502")
    assert out["positions"] == POSITIONS, "the raw records are kept"

    # Without the summaries, the rows are written with no mark, funding rate or option greeks.
    paradex.fail = {"/v1/markets/summary": 502}
    code, out, err = run(jwt_env(paradex))
    assert code == 0, err
    btc, _, option = out["_normalized"]["positions"]
    assert (btc["mark_price"], btc["funding_rate"], btc["delta"]) == (None, None, "0.5")
    assert (option["delta"], option["vega"]) == (None, None)
    assert "market_summaries" in out["_errors"]
