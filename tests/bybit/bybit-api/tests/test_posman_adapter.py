"""The posman adapter for Bybit (posman/account/fetch.mjs), against a local Bybit.

The adapter is run as the posman runner runs it, `node fetch.mjs` with the
runner's environment, pointed at a stand-in for Bybit's v5 REST API over plain
HTTP (POSMAN_BYBIT_API_URL, the adapter's one test hook). Nothing here reaches
Bybit or a credential proxy.

The stand-in plays the proxy's half of signing too: every signed request must
carry the placeholder in X-BAPI-SIGN and, base64 in X-Dime-Sign-<label>,
exactly `timestamp + api key + recv window + query string` as Bybit's guide
defines the signed bytes, with the query string as it arrived on the wire.
A request that does not is answered 401, which the adapter reports as a
refused credential, so a signing mistake fails the run loudly.

No dependencies: CI runs it as `python3 <file>` and reads the tally.
"""

import base64
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
    / "apps/bybit/skills/bybit-api/posman/account/fetch.mjs"
)

API_KEY = "Xk9pPUBLICkey"
CRED = "CRED_BYBIT_MAINNET_SECRET"
PLACEHOLDER = "sign-bybit-mainnet-secret-7Kj2mQx9vBn4rTz8"
EXPIRY_MS = "1793347200000"  # 2026-10-30T08:00:00Z

SIGNED_PREFIXES = ("/v5/position/", "/v5/order/", "/v5/account/")

INSTRUMENTS = {
    ("linear", "BTCUSDT"): {"symbol": "BTCUSDT", "contractType": "LinearPerpetual", "baseCoin": "BTC",
                            "quoteCoin": "USDT", "settleCoin": "USDT", "deliveryTime": "0", "fundingInterval": 480},
    ("linear", "ETHUSDT"): {"symbol": "ETHUSDT", "contractType": "LinearPerpetual", "baseCoin": "ETH",
                            "quoteCoin": "USDT", "settleCoin": "USDT", "deliveryTime": "0", "fundingInterval": 240},
    ("linear", "BTCPERP"): {"symbol": "BTCPERP", "contractType": "LinearPerpetual", "baseCoin": "BTC",
                            "quoteCoin": "USDC", "settleCoin": "USDC", "deliveryTime": "0", "fundingInterval": 480},
    ("linear", "BTC-30OCT26"): {"symbol": "BTC-30OCT26", "contractType": "LinearFutures", "baseCoin": "BTC",
                                "quoteCoin": "USDC", "settleCoin": "USDC", "deliveryTime": EXPIRY_MS},
    ("inverse", "BTCUSD"): {"symbol": "BTCUSD", "contractType": "InversePerpetual", "baseCoin": "BTC",
                            "quoteCoin": "USD", "settleCoin": "BTC", "deliveryTime": "0", "fundingInterval": 480},
    ("option", "BTC-30OCT26-96000-C-USDT"): {"symbol": "BTC-30OCT26-96000-C-USDT", "optionsType": "Call",
                                             "baseCoin": "BTC", "quoteCoin": "USDT", "settleCoin": "USDT",
                                             "deliveryTime": EXPIRY_MS},
    ("spot", "BTCUSDT"): {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT"},
    # OLDUSDT is not listed: delisted, so the adapter has only its symbol.
}

TICKERS = {
    ("linear", "BTCUSDT"): {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingIntervalHour": "8"},
    ("linear", "ETHUSDT"): {"symbol": "ETHUSDT", "fundingRate": "-0.00008", "fundingIntervalHour": "4"},
    # No fundingIntervalHour: the instrument's fundingInterval (minutes) says 8 hours.
    ("linear", "BTCPERP"): {"symbol": "BTCPERP", "fundingRate": "0.0002"},
    ("linear", "BTC-30OCT26"): {"symbol": "BTC-30OCT26", "fundingRate": ""},
    ("inverse", "BTCUSD"): {"symbol": "BTCUSD", "fundingRate": "0.0001", "fundingIntervalHour": "8"},
    ("option", "BTC-30OCT26-96000-C-USDT"): {"symbol": "BTC-30OCT26-96000-C-USDT", "delta": "0.4",
                                             "gamma": "0.00002", "vega": "30.5", "theta": "-12.25", "markIv": "0.55"},
}

POS_BTC = {"symbol": "BTCUSDT", "side": "Buy", "size": "0.5", "avgPrice": "64000", "markPrice": "65000",
           "liqPrice": "50000", "unrealisedPnl": "500", "curRealisedPnl": "-3.2", "leverage": "5",
           "positionValue": "32000", "updatedTime": "1762329600000", "positionIdx": 0}
POS_ETH = {"symbol": "ETHUSDT", "side": "Sell", "size": "2", "avgPrice": "3000", "markPrice": "2950",
           "liqPrice": "", "unrealisedPnl": "100", "leverage": "10", "positionValue": "6000",
           "updatedTime": "1762329600000", "positionIdx": 0}
POS_EMPTY = {"symbol": "SOLUSDT", "side": "", "size": "0", "avgPrice": "0", "markPrice": "150"}
POS_PERP_USDC = {"symbol": "BTCPERP", "side": "Buy", "size": "0.1", "avgPrice": "64000", "markPrice": "65000",
                 "liqPrice": "1000", "unrealisedPnl": "100", "leverage": "2", "updatedTime": "1762329600000"}
POS_FUTURE = {"symbol": "BTC-30OCT26", "side": "Sell", "size": "0.2", "avgPrice": "66000", "markPrice": "65500",
              "liqPrice": "90000", "unrealisedPnl": "100", "leverage": "3", "updatedTime": "1762329600000"}
POS_INVERSE = {"symbol": "BTCUSD", "side": "Buy", "size": "1000", "positionValue": "0.016", "avgPrice": "62500",
               "markPrice": "65000", "liqPrice": "40000", "unrealisedPnl": "0.0006", "leverage": "2",
               "updatedTime": "1762329600000"}
POS_OPTION = {"symbol": "BTC-30OCT26-96000-C-USDT", "side": "Sell", "size": "0.5", "avgPrice": "1500",
              "markPrice": "1200", "liqPrice": "0", "unrealisedPnl": "150", "leverage": "1",
              "delta": "-0.2", "updatedTime": "1762329600000"}

ORD_LIMIT = {"orderId": "o-1", "orderLinkId": "mine-1", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Limit",
             "stopOrderType": "", "price": "60000", "qty": "0.1", "cumExecQty": "0", "leavesQty": "0.1",
             "orderStatus": "New", "timeInForce": "GTC", "triggerPrice": "0", "reduceOnly": False,
             "createdTime": "1762329600000"}
ORD_STOP = {"orderId": "o-2", "orderLinkId": "", "symbol": "BTCUSDT", "side": "Sell", "orderType": "Market",
            "stopOrderType": "Stop", "price": "0", "qty": "0.1", "cumExecQty": "0", "leavesQty": "0.1",
            "orderStatus": "Untriggered", "timeInForce": "IOC", "triggerPrice": "58000", "reduceOnly": True,
            "createdTime": "1762329600000"}
ORD_OLD = {"orderId": "o-3", "orderLinkId": "", "symbol": "OLDUSDT", "side": "Buy", "orderType": "Limit",
           "stopOrderType": "", "price": "1.5", "qty": "10", "cumExecQty": "0", "leavesQty": "10",
           "orderStatus": "New", "timeInForce": "PostOnly", "triggerPrice": "", "reduceOnly": False,
           "createdTime": "1762329600000"}
ORD_INVERSE = {"orderId": "o-4", "orderLinkId": "", "symbol": "BTCUSD", "side": "Sell", "orderType": "Limit",
               "stopOrderType": "TakeProfit", "price": "50000", "qty": "1000", "cumExecQty": "200",
               "leavesQty": "800", "orderStatus": "PartiallyFilled", "timeInForce": "GTC", "triggerPrice": "49000",
               "reduceOnly": True, "createdTime": "1762329600000"}
ORD_OPTION = {"orderId": "o-5", "orderLinkId": "", "symbol": "BTC-30OCT26-96000-C-USDT", "side": "Sell",
              "orderType": "Limit", "stopOrderType": "", "price": "1500", "qty": "0.5", "cumExecQty": "0.2",
              "leavesQty": "0.3", "orderStatus": "PartiallyFilled", "timeInForce": "PostOnly",
              "triggerPrice": "", "reduceOnly": False, "createdTime": "1762329600000"}
ORD_SPOT = {"orderId": "o-6", "orderLinkId": "", "symbol": "BTCUSDT", "side": "Sell", "orderType": "Limit",
            "stopOrderType": "", "price": "70000", "qty": "0.01", "cumExecQty": "0", "leavesQty": "0.01",
            "orderStatus": "New", "timeInForce": "GTC", "triggerPrice": "0", "createdTime": "1762329600000"}

WALLET = {
    "accountType": "UNIFIED",
    "totalEquity": "13414.17",
    "totalAvailableBalance": "9840.1",
    "totalInitialMargin": "3000",
    "totalMaintenanceMargin": "800",
    "coin": [
        {"coin": "USDT", "walletBalance": "1000.5", "equity": "1500.5"},
        {"coin": "BTC", "walletBalance": "-0.1", "borrowAmount": "0.1"},
        {"coin": "ETH", "walletBalance": ""},
    ],
}


class Bybit:
    """What the stand-in answers, and what it was asked. Reset per run."""

    def __init__(self, label="bybit-mainnet-secret", overrides=None):
        self.label = label  # the X-Dime-Sign-<label> a signed request must carry
        self.requests = []
        self.overrides = overrides or {}  # (path, category or None) -> (status, body text)
        self.signing_errors = []


STATE = Bybit()


def ok(result):
    return 200, json.dumps({"retCode": 0, "retMsg": "OK", "result": result, "time": 1})


def answer(path, q):
    category = q.get("category")
    if path == "/v5/position/list":
        if category == "linear" and q.get("settleCoin") == "USDT":
            if q.get("cursor") == "abc%3D1":
                return ok({"category": "linear", "list": [POS_ETH], "nextPageCursor": ""})
            return ok({"category": "linear", "list": [POS_BTC, POS_EMPTY], "nextPageCursor": "abc%3D1"})
        if category == "linear" and q.get("settleCoin") == "USDC":
            return ok({"category": "linear", "list": [POS_PERP_USDC, POS_FUTURE], "nextPageCursor": ""})
        if category == "inverse":
            return ok({"category": "inverse", "list": [POS_INVERSE], "nextPageCursor": ""})
        if category == "option":
            return ok({"category": "option", "list": [POS_OPTION], "nextPageCursor": ""})
    if path == "/v5/order/realtime":
        lists = {
            ("linear", "USDT"): [ORD_LIMIT, ORD_STOP, ORD_OLD],
            ("linear", "USDC"): [],
            ("inverse", None): [ORD_INVERSE],
            ("option", None): [ORD_OPTION],
            ("spot", None): [ORD_SPOT],
        }
        return ok({"category": category, "list": lists.get((category, q.get("settleCoin")), []), "nextPageCursor": ""})
    if path == "/v5/account/wallet-balance":
        return ok({"list": [WALLET]})
    if path == "/v5/account/info":
        return ok({"marginMode": "REGULAR_MARGIN", "unifiedMarginStatus": 5})
    if path == "/v5/market/instruments-info":
        inst = INSTRUMENTS.get((category, q.get("symbol")))
        return ok({"category": category, "list": [inst] if inst else []})
    if path == "/v5/market/tickers":
        t = TICKERS.get((category, q.get("symbol")))
        return ok({"category": category, "list": [t] if t else []})
    return 404, "not found"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path, _, raw_query = self.path.partition("?")
        # The cursor is matched as sent: parse_qsl would decode it.
        q = dict(pair.split("=", 1) for pair in raw_query.split("&") if pair)
        headers = {k.lower(): v for k, v in self.headers.items()}
        STATE.requests.append({"path": path, "query": raw_query, "q": q, "headers": headers})
        signed = path.startswith(SIGNED_PREFIXES)
        if signed:
            sign = headers.get(f"x-dime-sign-{STATE.label}")
            want = (
                headers.get("x-bapi-timestamp", "") + API_KEY + headers.get("x-bapi-recv-window", "") + raw_query
            )
            problems = []
            if headers.get("x-bapi-sign") != PLACEHOLDER:
                problems.append("X-BAPI-SIGN is not the placeholder")
            if headers.get("x-bapi-api-key") != API_KEY:
                problems.append("X-BAPI-API-KEY is not the key")
            if headers.get("x-bapi-recv-window") != "5000":
                problems.append("no recv window")
            if not sign or base64.b64decode(sign).decode() != want:
                problems.append(f"signed bytes are not timestamp+key+recvWindow+query ({raw_query})")
            if problems:
                STATE.signing_errors.append(f"{path}: {'; '.join(problems)}")
                return self.send(401, "")
        elif any(k.startswith("x-dime-sign") or k.startswith("x-bapi") for k in headers):
            STATE.signing_errors.append(f"{path}: a public read carries signing headers")
        override = STATE.overrides.get((path, q.get("category"))) or STATE.overrides.get((path, None))
        status, body = override if override else answer(path, q)
        self.send(status, body)

    def send(self, status, body):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{SERVER.server_address[1]}"


def run(cred=CRED, placeholder=PLACEHOLDER, meta=None, params=None, overrides=None):
    global STATE
    STATE = Bybit(cred.removeprefix("CRED_").lower().replace("_", "-"), overrides)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "POSMAN_SOURCE": "bybit-main",
        "POSMAN_VENUE": "bybit",
        "POSMAN_CRED": cred,
        "POSMAN_BYBIT_API_URL": URL,
    }
    if cred:
        env[cred] = placeholder
        env[f"{cred}_META"] = json.dumps({"api_key": API_KEY} if meta is None else meta)
    for k, v in (params or {}).items():
        env[f"POSMAN_PARAM_{k}"] = v
    proc = subprocess.run(
        ["node", ADAPTER.name], cwd=ADAPTER.parent, env=env, capture_output=True, text=True, timeout=60
    )
    out = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else None
    return proc.returncode, out, proc.stderr, STATE


passed = 0
failed = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(f"{name}{f': {detail}' if detail else ''}")


def by(rows, **match):
    return [r for r in rows if all(r.get(k) == v for k, v in match.items())]


# ---- a whole fetch ----

code, out, err, seen = run()
check("a clean fetch exits 0", code == 0, err)
check("every signed request was signed as Bybit defines it", not seen.signing_errors, seen.signing_errors)
out = out or {}

check("the datasets, in order",
      list(out) == ["positions", "open_orders", "balances", "account", "account_info", "_normalized"], list(out))

paths = [(r["path"], r["query"]) for r in seen.requests]
check("linear positions are asked by settle coin, 200 a page",
      ("/v5/position/list", "category=linear&settleCoin=USDT&limit=200") in paths
      and ("/v5/position/list", "category=linear&settleCoin=USDC&limit=200") in paths)
check("the next page sends Bybit's cursor as it came, not encoded again",
      ("/v5/position/list", "category=linear&settleCoin=USDT&limit=200&cursor=abc%3D1") in paths, paths)
check("inverse and option positions are asked without a settle coin",
      ("/v5/position/list", "category=inverse&limit=200") in paths
      and ("/v5/position/list", "category=option&limit=200") in paths)
check("open orders cover spot too, 50 a page",
      ("/v5/order/realtime", "category=spot&limit=50") in paths)
check("the wallet is the unified account's",
      ("/v5/account/wallet-balance", "accountType=UNIFIED") in paths)
check("an option's instrument is asked with its base coin",
      ("/v5/market/instruments-info", "category=option&symbol=BTC-30OCT26-96000-C-USDT&baseCoin=BTC") in paths)
check("tickers are read for positions only",
      not any(p == "/v5/market/tickers" and "OLDUSDT" in q for p, q in paths)
      and ("/v5/market/tickers", "category=linear&symbol=BTCUSDT") in paths)

raw = out.get("positions", [])
check("raw positions are Bybit's records, each with its category, empty ones left out",
      [p["symbol"] for p in raw] == ["BTCUSDT", "ETHUSDT", "BTCPERP", "BTC-30OCT26", "BTCUSD",
                                     "BTC-30OCT26-96000-C-USDT"]
      and raw[0] == {"category": "linear", **POS_BTC}, [p.get("symbol") for p in raw])
check("raw open orders keep every category's", len(out.get("open_orders", [])) == 6
      and out["open_orders"][-1] == {"category": "spot", **ORD_SPOT})
check("balances are the unified wallet's coins", out.get("balances") == WALLET["coin"])
check("account is the wallet's account-wide figures", out.get("account") == {k: v for k, v in WALLET.items() if k != "coin"})
check("account_info is Bybit's", out.get("account_info", {}).get("marginMode") == "REGULAR_MARGIN")

norm = out.get("_normalized", {})
pos = norm.get("positions", [])
check("one row per open position", [r["symbol"] for r in pos] == [p["symbol"] for p in raw])

btc = (by(pos, symbol="BTCUSDT") or [{}])[0]
check("a USDT perp", btc == {
    "symbol": "BTCUSDT", "underlying": "BTC", "quote_asset": "USDT", "settle_asset": "USDT", "kind": "perp",
    "contract": "linear", "expiry": None, "side": "long", "size": "0.5", "entry_price": "64000",
    "mark_price": "65000", "liquidation_price": "50000", "unrealized_pnl": "500", "funding_pnl": None,
    "pnl_asset": "USDT", "margin_mode": "cross", "leverage": "5", "updated_at": "2025-11-05T08:00:00.000Z",
    "funding_rate": "0.0000125", "delta": "0.5", "gamma": "0", "vega": "0", "theta": "0",
}, btc)
eth = (by(pos, symbol="ETHUSDT") or [{}])[0]
check("a short: negative size and delta, no liquidation level, its own funding hours",
      eth.get("size") == "-2" and eth.get("delta") == "-2" and eth.get("liquidation_price") is None
      and eth.get("funding_rate") == "-0.00002", eth)
perp = (by(pos, symbol="BTCPERP") or [{}])[0]
check("a USDC perp's funding period from the instrument when the ticker has none",
      perp.get("settle_asset") == "USDC" and perp.get("pnl_asset") == "USDC" and perp.get("funding_rate") == "0.000025",
      perp)
fut = (by(pos, symbol="BTC-30OCT26") or [{}])[0]
check("a dated future: its expiry, no funding rate",
      fut.get("kind") == "future" and fut.get("expiry") == "2026-10-30T08:00:00.000Z"
      and "funding_rate" not in fut and fut.get("delta") == "-0.2", fut)
inv = (by(pos, symbol="BTCUSD") or [{}])[0]
check("an inverse perp counts coins (Bybit's positionValue), PnL in the coin",
      inv.get("contract") == "inverse" and inv.get("size") == "0.016" and inv.get("pnl_asset") == "BTC"
      and inv.get("delta") == "0.016" and inv.get("settle_asset") == "BTC", inv)
opt = (by(pos, symbol="BTC-30OCT26-96000-C-USDT") or [{}])[0]
check("an option: strike, type and expiry; greeks are the ticker's per-unit greeks times the signed size",
      opt.get("kind") == "option" and opt.get("strike") == "96000" and opt.get("option_type") == "call"
      and opt.get("expiry") == "2026-10-30T08:00:00.000Z" and opt.get("size") == "-0.5"
      and opt.get("delta") == "-0.2" and opt.get("gamma") == "-0.00001" and opt.get("vega") == "-15.25"
      and opt.get("theta") == "6.125" and opt.get("iv") == "0.55" and opt.get("leverage") is None
      and opt.get("liquidation_price") is None, opt)

orders = norm.get("open_orders", [])
check("one row per open order", [r["order_id"] for r in orders] == ["o-1", "o-2", "o-3", "o-4", "o-5", "o-6"],
      [r.get("order_id") for r in orders])
lim = (by(orders, order_id="o-1") or [{}])[0]
check("a limit order", lim == {
    "symbol": "BTCUSDT", "underlying": "BTC", "quote_asset": "USDT", "settle_asset": "USDT", "kind": "perp",
    "contract": "linear", "expiry": None, "order_id": "o-1", "client_order_id": "mine-1", "side": "buy",
    "type": "limit", "venue_type": "Limit", "status": "open", "venue_status": "New", "price": "60000",
    "trigger_price": None, "size": "0.1", "filled_size": "0", "remaining_size": "0.1", "time_in_force": "gtc",
    "reduce_only": False, "created_at": "2025-11-05T08:00:00.000Z", "expires_at": None,
}, lim)
stop = (by(orders, order_id="o-2") or [{}])[0]
check("a conditional market order is an untriggered stop_market with no price",
      stop.get("type") == "stop_market" and stop.get("status") == "untriggered" and stop.get("price") is None
      and stop.get("trigger_price") == "58000" and stop.get("venue_type") == "Stop"
      and stop.get("reduce_only") is True and stop.get("time_in_force") == "ioc", stop)
old = (by(orders, order_id="o-3") or [{}])[0]
check("a symbol Bybit no longer lists: what its name says, as kind other",
      old.get("kind") == "other" and old.get("underlying") == "OLD" and old.get("settle_asset") == "USDT"
      and old.get("time_in_force") == "post_only", old)
inv_o = (by(orders, order_id="o-4") or [{}])[0]
check("an inverse order's USD is turned into coins at its price",
      inv_o.get("size") == "0.02" and inv_o.get("filled_size") == "0.004" and inv_o.get("remaining_size") == "0.016"
      and inv_o.get("type") == "take_profit_limit" and inv_o.get("status") == "partially_filled", inv_o)
spot = (by(orders, order_id="o-6") or [{}])[0]
check("a spot order: kind spot, no contract or settle asset",
      spot.get("kind") == "spot" and spot.get("contract") is None and spot.get("settle_asset") is None
      and spot.get("reduce_only") is None, spot)

check("balances: each coin's wallet balance, borrowed negative, blanks left out",
      norm.get("balances") == [{"asset": "USDT", "total": "1000.5", "updated_at": None},
                               {"asset": "BTC", "total": "-0.1", "updated_at": None}], norm.get("balances"))

# Every value posman reads as a decimal is one.
decimals = [v for r in pos + orders + norm.get("balances", []) for k, v in r.items()
            if isinstance(v, str) and k not in ("symbol", "underlying", "quote_asset", "settle_asset", "kind",
                                                 "contract", "side", "pnl_asset", "margin_mode", "order_id",
                                                 "client_order_id", "type", "venue_type", "status",
                                                 "venue_status", "time_in_force", "option_type", "asset")
            and not k.endswith(("_at", "expiry"))]
check("decimals have no exponent", all(set(v) <= set("-0123456789.") for v in decimals), decimals)


# ---- one dataset failing ----

code, out, err, _ = run(overrides={("/v5/order/realtime", "spot"): (500, "upstream down")})
check("a failing endpoint fails only its dataset", code == 0 and "open_orders" not in out
      and "positions" in out and "HTTP 500" in out.get("_errors", {}).get("open_orders", ""), (code, err))
check("and its rows are left out, so they keep their last copy",
      "open_orders" not in out.get("_normalized", {}) and "positions" in out.get("_normalized", {}))

code, out, err, _ = run(overrides={("/v5/account/wallet-balance", None): (200, json.dumps({"retCode": 10016, "retMsg": "Server error."}))})
check("a wallet that fails takes balances and account with it, nothing else",
      code == 0 and "balances" in out.get("_errors", {}) and "account" in out.get("_errors", {})
      and "positions" in out and "balances" not in out.get("_normalized", {}), (code, out and out.get("_errors")))

code, out, err, _ = run(overrides={("/v5/market/tickers", None): (500, "")})
check("without tickers the rows still map, funding and greeks null",
      code == 0 and "tickers" in out.get("_errors", {})
      and (by(out["_normalized"]["positions"], symbol="BTCUSDT") or [{}])[0].get("funding_rate") is None
      and (by(out["_normalized"]["positions"], symbol="BTC-30OCT26-96000-C-USDT") or [{}])[0].get("delta") is None,
      (code, err))

code, out, err, _ = run(overrides={("/v5/market/instruments-info", None): (502, "bad gateway")})
check("without instruments, positions and orders keep their last rows; balances still map",
      code == 0 and "instruments" in out.get("_errors", {})
      and set(out["_normalized"]) == {"balances"} and "positions" in out, (code, out and out.get("_errors")))


# ---- the credential refused ----

for status, body, why in [
    (200, json.dumps({"retCode": 10003, "retMsg": "API key is invalid."}), "10003"),
    (200, json.dumps({"retCode": 10004, "retMsg": "error sign!"}), "10004"),
    (200, json.dumps({"retCode": 10005, "retMsg": "Permission denied"}), "10005"),
    (200, json.dumps({"retCode": 33004, "retMsg": "api key expired"}), "33004"),
    (401, "", "an empty 401"),
    (403, "credential not allowed for this route", "the proxy's 403"),
]:
    code, out, err, _ = run(overrides={("/v5/account/info", None): (status, body)})
    check(f"{why} is a refused credential: exit 3", code == 3 and out is None, (code, err))

code, out, err, _ = run(overrides={("/v5/account/info", None): (403, "The Amazon CloudFront distribution is configured to block access from your country")})
check("Bybit's region block is not the credential", code == 0 and "account_info" in out.get("_errors", {}), (code, err))

code, out, err, _ = run(overrides={("/v5/account/info", None): (200, json.dumps({"retCode": 10002, "retMsg": "timestamp out of window"}))})
check("a clock out of the recv window is not the credential", code == 0 and "10002" in out["_errors"]["account_info"]
      and (by(out["_normalized"]["positions"], symbol="BTCUSDT") or [{}])[0].get("margin_mode") is None, (code, err))


# ---- the environment, and a source set up wrong ----

code, out, err, seen = run(cred="CRED_BYBIT_DEMO_SECRET", placeholder=PLACEHOLDER)
check("demo trading asks for no options", code == 0
      and not any(r["q"].get("category") == "option" for r in seen.requests), (code, err, seen.signing_errors))

code, out, err, _ = run(cred="CRED_MY_BYBIT", params={})
check("an environment the label does not name is a set-up error", code == 2 and "ENVIRONMENT" in err, (code, err))

code, out, err, seen = run(cred="CRED_MY_BYBIT", params={"ENVIRONMENT": "mainnet"})
check("ENVIRONMENT names it, and the sign header follows the credential's own name",
      code == 0 and not seen.signing_errors
      and "x-dime-sign-my-bybit" in seen.requests[0]["headers"], (code, err, seen.signing_errors))

code, out, err, _ = run(meta={})
check("no API key in the credential's details is a set-up error", code == 2 and "API key" in err, (code, err))

code, out, err, _ = run(cred="")
check("no credential is a set-up error", code == 2, (code, err))


SERVER.shutdown()
print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
