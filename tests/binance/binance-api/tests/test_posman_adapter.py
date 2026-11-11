"""The posman adapter for Binance (posman/account/fetch.mjs), against a local Binance.

The adapter is run as the posman runner runs it: `node fetch.mjs` with the
runner's environment, pointed at a stand-in for both Binance hosts over plain
HTTP (POSMAN_BINANCE_API_URL, the adapter's one test hook). Each case records
what reached the stand-in, so the checks are on what Binance (and the
credential proxy in front of it) would receive. Nothing reaches Binance.

Every signed read is also held to app.yaml: it must match a route of the
signing credential, matched the way the proxy matches it, or the proxy would
pass the placeholder through unsigned. A public read must match none, or the
proxy would refuse it for carrying no placeholder.
"""

import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[4]
ADAPTER = ROOT / "apps/binance/skills/binance-api/posman/account/fetch.mjs"
MANIFEST = ROOT / "apps/binance/app.yaml"

PLACEHOLDER = "sign-binance-usdm-secret-0123456789abcdef"

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


# --- the stand-in ----------------------------------------------------------


class Stand:
    """Answers each path from `replies` ({path: (status, body)}) and records every request."""

    def __init__(self, replies):
        self.replies = replies
        self.seen = []
        stand = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                stand.seen.append({"path": url.path, "query": url.query, "headers": dict(self.headers)})
                status, body = stand.replies.get(url.path, (404, {"code": -5000, "msg": f"Path {url.path}, Method GET is invalid"}))
                raw = body if isinstance(body, str) else json.dumps(body)
                self.send_response(status)
                self.send_header("Content-Type", "text/plain" if isinstance(body, str) else "application/json")
                self.end_headers()
                self.wfile.write(raw.encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run(replies, cred="CRED_BINANCE_USDM_SECRET", params=None, hook=True):
    stand = Stand(replies)
    env = {"PATH": os.environ["PATH"], "POSMAN_SOURCE": "binance-usdm", "POSMAN_VENUE": "binance"}
    if cred is not None:
        env["POSMAN_CRED"] = cred
        env[cred] = PLACEHOLDER
    if hook:
        env["POSMAN_BINANCE_API_URL"] = stand.url
    for k, v in (params or {}).items():
        env[f"POSMAN_PARAM_{k}"] = v
    proc = subprocess.run(["node", str(ADAPTER)], capture_output=True, text=True, env=env, cwd=ADAPTER.parent, timeout=60)
    stand.close()
    out = None
    if proc.stdout.strip():
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            out = None
    return {"exit": proc.returncode, "out": out, "err": proc.stderr, "seen": stand.seen}


# --- app.yaml's routes, matched as the proxy matches them --------------------


def secret_routes():
    text = MANIFEST.read_text()
    block = text[text.index("  - id: secret\n"):]
    block = block[: block.index("    summary:")]
    return re.findall(r"- path: (\S+)", block)


def matches(pattern, path):
    if pattern.endswith("/*"):
        return path.startswith(pattern[:-1]) or path == pattern[:-2]
    regex = "".join("[^/]*" if part == "*" else re.escape(part) for part in re.split(r"(\*)", pattern))
    return re.fullmatch(regex, path) is not None


ROUTES = secret_routes()


def signed(req):
    return "signature=" in req["query"]


def sign_header(req):
    return req["headers"].get("X-Dime-Sign-binance-usdm-secret") or req["headers"].get("X-Dime-Sign-binance-spot-secret")


# --- fixtures: USD-M futures -------------------------------------------------

BTC = {
    "symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0.500", "entryPrice": "64000.0",
    "breakEvenPrice": "64025.6", "markPrice": "65000.10000000", "unRealizedProfit": "500.05000000",
    "liquidationPrice": "52010.98", "isolatedMargin": "0", "notional": "32500.05", "marginAsset": "USDT",
    "isolatedWallet": "0", "initialMargin": "1625", "maintMargin": "130", "positionInitialMargin": "1625",
    "openOrderInitialMargin": "0", "adl": 2, "bidNotional": "0", "askNotional": "0", "updateTime": 1762329600000,
}
ETH_DATED = {
    "symbol": "ETHUSDT_260327", "positionSide": "BOTH", "positionAmt": "-2.000", "entryPrice": "3500",
    "breakEvenPrice": "3499", "markPrice": "3450", "unRealizedProfit": "100", "liquidationPrice": "0",
    "isolatedMargin": "1380", "notional": "-6900", "marginAsset": "USDT", "isolatedWallet": "1380",
    "initialMargin": "1380", "maintMargin": "55", "positionInitialMargin": "1380", "openOrderInitialMargin": "0",
    "adl": 1, "bidNotional": "0", "askNotional": "0", "updateTime": 0,
}
XRP_LONG = dict(BTC, symbol="XRPUSDT", positionSide="LONG", positionAmt="100", entryPrice="0.5",
                markPrice="0.55", unRealizedProfit="5", liquidationPrice="0.1", updateTime=1762329600000)
XRP_SHORT = dict(XRP_LONG, positionSide="SHORT", positionAmt="-50", unRealizedProfit="-2.5", liquidationPrice="0.9")
OLD = dict(BTC, symbol="OLDCOINUSDT", positionAmt="10", marginAsset="USDT")
CLOSED = dict(BTC, symbol="SOLUSDT", positionAmt="0.000", unRealizedProfit="0")

LIMIT = {
    "orderId": 8389765, "symbol": "BTCUSDT", "status": "PARTIALLY_FILLED", "clientOrderId": "my-1",
    "price": "60000", "avgPrice": "60000", "origQty": "0.100", "executedQty": "0.040", "cumQuote": "2400",
    "timeInForce": "GTC", "type": "LIMIT", "reduceOnly": False, "closePosition": False, "side": "BUY",
    "positionSide": "BOTH", "stopPrice": "0", "workingType": "CONTRACT_PRICE", "priceProtect": False,
    "origType": "LIMIT", "priceMatch": "NONE", "selfTradePreventionMode": "EXPIRE_MAKER", "goodTillDate": 0,
    "time": 1762329600000, "updateTime": 1762329600000,
}
GTD = dict(LIMIT, orderId=8389766, symbol="ETHUSDT_260327", status="NEW", clientOrderId="", price="3600",
           origQty="1", executedQty="0", timeInForce="GTD", side="SELL", reduceOnly=True, goodTillDate=1762416000000)
MAKER = dict(LIMIT, orderId=8389767, status="NEW", timeInForce="GTX", executedQty="0")

CLOSE_STOP = {
    "algoId": 2146760, "clientAlgoId": "sl-1", "algoType": "CONDITIONAL", "orderType": "STOP_MARKET",
    "symbol": "BTCUSDT", "side": "SELL", "positionSide": "BOTH", "timeInForce": "GTE_GTC", "quantity": "0",
    "algoStatus": "NEW", "triggerPrice": "58000", "price": "0", "workingType": "MARK_PRICE",
    "closePosition": True, "priceProtect": True, "reduceOnly": False, "createTime": 1762329600000,
    "updateTime": 1762329600000, "triggerTime": 0, "goodTillDate": 0,
}
TP_LIMIT = dict(CLOSE_STOP, algoId=2146761, clientAlgoId="tp-1", orderType="TAKE_PROFIT", symbol="ETHUSDT_260327",
                side="BUY", quantity="1", triggerPrice="3000", price="2990", closePosition=False, reduceOnly=True,
                timeInForce="GTC")
TRAIL = dict(CLOSE_STOP, algoId=2146762, clientAlgoId="", orderType="TRAILING_STOP_MARKET", symbol="XRPUSDT",
             side="SELL", positionSide="LONG", quantity="40", triggerPrice="", activatePrice="0.6",
             callbackRate="1", closePosition=False)
ORPHAN_CLOSE = dict(CLOSE_STOP, algoId=2146763, symbol="ADAUSDT")

ACCOUNT = {
    "totalInitialMargin": "3005.00", "totalMaintMargin": "185.00", "totalWalletBalance": "1250.50",
    "totalUnrealizedProfit": "602.55", "totalMarginBalance": "1853.05", "totalPositionInitialMargin": "3005",
    "totalOpenOrderInitialMargin": "0", "totalCrossWalletBalance": "1250.50", "totalCrossUnPnl": "502.55",
    "availableBalance": "200.10", "maxWithdrawAmount": "200.10",
    "assets": [
        {"asset": "USDT", "walletBalance": "1250.50", "unrealizedProfit": "602.55", "marginBalance": "1853.05",
         "maintMargin": "185", "initialMargin": "3005", "positionInitialMargin": "3005", "openOrderInitialMargin": "0",
         "crossWalletBalance": "1250.50", "crossUnPnl": "502.55", "availableBalance": "200.10",
         "maxWithdrawAmount": "200.10", "updateTime": 1762329600000},
        {"asset": "USDC", "walletBalance": "0.00000000", "unrealizedProfit": "0", "marginBalance": "0",
         "maintMargin": "0", "initialMargin": "0", "positionInitialMargin": "0", "openOrderInitialMargin": "0",
         "crossWalletBalance": "0", "crossUnPnl": "0", "availableBalance": "0", "maxWithdrawAmount": "0",
         "updateTime": 0},
        {"asset": "BNB", "walletBalance": "0.5", "unrealizedProfit": "0", "marginBalance": "0.5",
         "maintMargin": "0", "initialMargin": "0", "positionInitialMargin": "0", "openOrderInitialMargin": "0",
         "crossWalletBalance": "0.5", "crossUnPnl": "0", "availableBalance": "0.5", "maxWithdrawAmount": "0.5",
         "updateTime": 0},
    ],
    "positions": [{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0.500"}],
}

EXCHANGE_INFO = {"symbols": [
    {"symbol": "BTCUSDT", "pair": "BTCUSDT", "contractType": "PERPETUAL", "deliveryDate": 4133404802000,
     "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT"},
    {"symbol": "ETHUSDT_260327", "pair": "ETHUSDT", "contractType": "CURRENT_QUARTER", "deliveryDate": 1774598400000,
     "baseAsset": "ETH", "quoteAsset": "USDT", "marginAsset": "USDT"},
    {"symbol": "XRPUSDT", "pair": "XRPUSDT", "contractType": "TRADIFI_PERPETUAL", "deliveryDate": 4133404802000,
     "baseAsset": "XRP", "quoteAsset": "USDT", "marginAsset": "USDT"},
]}
PREMIUM = [
    {"symbol": "BTCUSDT", "markPrice": "65000.1", "lastFundingRate": "0.00010000", "nextFundingTime": 1762358400000},
    {"symbol": "XRPUSDT", "markPrice": "0.55", "lastFundingRate": "-0.00004000", "nextFundingTime": 1762358400000},
    {"symbol": "ETHUSDT_260327", "markPrice": "3450", "lastFundingRate": "", "nextFundingTime": 0},
]
FUNDING_INFO = [{"symbol": "XRPUSDT", "adjustedFundingRateCap": "0.02", "adjustedFundingRateFloor": "-0.02",
                 "fundingIntervalHours": 4, "disclaimer": False}]
SYMBOL_CONFIG = [
    {"symbol": "BTCUSDT", "marginType": "CROSSED", "isAutoAddMargin": "false", "leverage": 20, "maxNotionalValue": "1000000"},
    {"symbol": "ETHUSDT_260327", "marginType": "ISOLATED", "isAutoAddMargin": "false", "leverage": 5, "maxNotionalValue": "250000"},
]

FUTURES = {
    "/fapi/v3/positionRisk": (200, [BTC, ETH_DATED, XRP_LONG, XRP_SHORT, OLD, CLOSED]),
    "/fapi/v1/openOrders": (200, [LIMIT, GTD, MAKER]),
    "/fapi/v1/openAlgoOrders": (200, [CLOSE_STOP, TP_LIMIT, TRAIL, ORPHAN_CLOSE]),
    "/fapi/v3/account": (200, ACCOUNT),
    "/fapi/v1/exchangeInfo": (200, EXCHANGE_INFO),
    "/fapi/v1/premiumIndex": (200, PREMIUM),
    "/fapi/v1/fundingInfo": (200, FUNDING_INFO),
    "/fapi/v1/symbolConfig": (200, SYMBOL_CONFIG),
}

# --- USD-M futures: the requests ----------------------------------------------

got = run(FUTURES)
check("futures: the run exits 0", got["exit"] == 0)
out = got["out"] or {}
seen = got["seen"]

paths = sorted(r["path"] for r in seen)
check("futures: reads the futures endpoints, and only those", paths == sorted(FUTURES))
signed_reqs = [r for r in seen if signed(r)]
public_reqs = [r for r in seen if not signed(r)]
check("futures: five signed reads", sorted(r["path"] for r in signed_reqs) == sorted([
    "/fapi/v3/positionRisk", "/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders", "/fapi/v3/account", "/fapi/v1/symbolConfig",
]))
for r in signed_reqs:
    q = r["query"]
    check(f"signed {r['path']}: the placeholder is the last parameter", q.endswith(f"&signature={PLACEHOLDER}"))
    body = q[: -len(f"&signature={PLACEHOLDER}")]
    check(f"signed {r['path']}: X-Dime-Sign carries the exact bytes sent, base64",
          sign_header(r) == base64.b64encode(body.encode()).decode())
    params = urllib.parse.parse_qs(body)
    check(f"signed {r['path']}: a timestamp and a recvWindow", "timestamp" in params and params.get("recvWindow") == ["10000"])
    check(f"signed {r['path']}: never an API key (the proxy attaches it)",
          not any(k.lower() == "x-mbx-apikey" for k in r["headers"]))
    check(f"signed {r['path']}: is a route of the signing credential", any(matches(p, r["path"]) for p in ROUTES))
for r in public_reqs:
    check(f"public {r['path']}: no placeholder, no signing header",
          PLACEHOLDER not in r["query"] and sign_header(r) is None)
    check(f"public {r['path']}: is no signed route (the proxy would demand the placeholder)",
          not any(matches(p, r["path"]) for p in ROUTES))

# --- USD-M futures: the raw datasets --------------------------------------------

check("futures: positions are Binance's records, closed ones left out",
      out.get("positions") == [BTC, ETH_DATED, XRP_LONG, XRP_SHORT, OLD])
check("futures: open_orders are Binance's records, unchanged", out.get("open_orders") == [LIMIT, GTD, MAKER])
check("futures: open_algo_orders are Binance's records, unchanged",
      out.get("open_algo_orders") == [CLOSE_STOP, TP_LIMIT, TRAIL, ORPHAN_CLOSE])
check("futures: balances are the account's assets with a wallet balance",
      out.get("balances") == [ACCOUNT["assets"][0], ACCOUNT["assets"][2]])
acct = out.get("account") or {}
check("futures: account is the totals, without the lists balances and positions carry",
      "assets" not in acct and "positions" not in acct and acct.get("totalMarginBalance") == "1853.05")
check("futures: no _errors when everything answered", "_errors" not in out)

# --- USD-M futures: posman's rows ------------------------------------------------

norm = out.get("_normalized") or {}
pos = {(p["symbol"], p["side"]): p for p in norm.get("positions", [])}
check("futures: one row per open position (hedge mode's two sides apart)", len(norm.get("positions", [])) == 5)
btc = pos.get(("BTCUSDT", "long"), {})
check("futures: a perpetual's instrument", {k: btc.get(k) for k in ("kind", "underlying", "quote_asset", "settle_asset", "contract", "expiry")}
      == {"kind": "perp", "underlying": "BTC", "quote_asset": "USDT", "settle_asset": "USDT", "contract": "linear", "expiry": None})
check("futures: size, prices and PnL as Binance gives them",
      (btc.get("size"), btc.get("entry_price"), btc.get("mark_price"), btc.get("liquidation_price"), btc.get("unrealized_pnl"), btc.get("pnl_asset"))
      == ("0.500", "64000.0", "65000.10000000", "52010.98", "500.05000000", "USDT"))
check("futures: funding PnL is null (Binance gives none on the position)", "funding_pnl" in btc and btc["funding_pnl"] is None)
check("futures: an 8-hour rate per hour", btc.get("funding_rate") == "0.0000125")
check("futures: leverage and margin mode from the symbol config", (btc.get("leverage"), btc.get("margin_mode")) == ("20", "cross"))
check("futures: a perp's greeks", (btc.get("delta"), btc.get("gamma"), btc.get("vega"), btc.get("theta")) == ("0.500", "0", "0", "0"))
check("futures: updated_at in ISO UTC", btc.get("updated_at") == "2025-11-05T08:00:00.000Z")

eth = pos.get(("ETHUSDT_260327", "short"), {})
check("futures: a dated future with its delivery as expiry",
      (eth.get("kind"), eth.get("underlying"), eth.get("expiry")) == ("future", "ETH", "2026-03-27T08:00:00.000Z"))
check("futures: a short is negative", (eth.get("size"), eth.get("delta")) == ("-2.000", "-2.000"))
check("futures: no liquidation level is null, never 0", eth.get("liquidation_price") is None)
check("futures: a future has no funding rate", eth.get("funding_rate") is None)
check("futures: an isolated position", (eth.get("margin_mode"), eth.get("leverage")) == ("isolated", "5"))
check("futures: no update time is null", eth.get("updated_at") is None)

xl, xs = pos.get(("XRPUSDT", "long"), {}), pos.get(("XRPUSDT", "short"), {})
check("futures: hedge mode's long and short", (xl.get("size"), xs.get("size")) == ("100", "-50"))
check("futures: a TradFi perpetual is a perp", xl.get("kind") == "perp")
check("futures: a 4-hour rate per hour", xl.get("funding_rate") == "-0.00001")
check("futures: no symbol config for it, so no leverage or margin mode", (xl.get("leverage"), xl.get("margin_mode")) == (None, None))

old = pos.get(("OLDCOINUSDT", "long"), {})
check("futures: an unlisted symbol is kind other, its base the symbol less its margin asset",
      (old.get("kind"), old.get("underlying"), old.get("settle_asset")) == ("other", "OLDCOIN", "USDT"))

orders = {o["order_id"]: o for o in norm.get("open_orders", [])}
check("futures: one row per order, algo orders included, an orphan close-position stop left out",
      sorted(orders) == ["2146760", "2146761", "2146762", "8389765", "8389766", "8389767"])
lim = orders.get("8389765", {})
check("futures: a partly filled limit order",
      {k: lim.get(k) for k in ("type", "venue_type", "status", "venue_status", "price", "trigger_price", "size", "filled_size", "remaining_size", "time_in_force", "reduce_only", "client_order_id", "side")}
      == {"type": "limit", "venue_type": "LIMIT", "status": "partially_filled", "venue_status": "PARTIALLY_FILLED",
          "price": "60000", "trigger_price": None, "size": "0.100", "filled_size": "0.040", "remaining_size": "0.06",
          "time_in_force": "gtc", "reduce_only": False, "client_order_id": "my-1", "side": "buy"})
gtd = orders.get("8389766", {})
check("futures: a GTD order's expiry, an empty client id as null",
      (gtd.get("time_in_force"), gtd.get("expires_at"), gtd.get("client_order_id"), gtd.get("reduce_only"), gtd.get("kind"))
      == ("gtd", "2025-11-06T08:00:00.000Z", None, True, "future"))
check("futures: GTX is post-only", orders.get("8389767", {}).get("time_in_force") == "post_only")
stop = orders.get("2146760", {})
check("futures: a close-position stop is the position it closes, untriggered and reduce-only",
      {k: stop.get(k) for k in ("type", "venue_type", "status", "venue_status", "size", "trigger_price", "price", "reduce_only", "side")}
      == {"type": "stop_market", "venue_type": "STOP_MARKET", "status": "untriggered", "venue_status": "NEW",
          "size": "0.500", "trigger_price": "58000", "price": None, "reduce_only": True, "side": "sell"})
tp = orders.get("2146761", {})
check("futures: a take-profit limit keeps its limit price",
      (tp.get("type"), tp.get("price"), tp.get("trigger_price"), tp.get("size")) == ("take_profit_limit", "2990", "3000", "1"))
trail = orders.get("2146762", {})
check("futures: a trailing stop has no trigger price until it moves",
      (trail.get("type"), trail.get("trigger_price"), trail.get("client_order_id")) == ("trailing_stop", None, None))

bals = norm.get("balances", [])
check("futures: balances are the wallet balance, without unrealized PnL",
      bals == [{"asset": "USDT", "total": "1250.50", "updated_at": "2025-11-05T08:00:00.000Z"},
               {"asset": "BNB", "total": "0.5", "updated_at": None}])

# Every row: decimals as strings, no exponents; times ISO.
DECIMAL = re.compile(r"^-?\d{1,40}(\.\d{1,40})?$")
bad = [
    (kind, k, v)
    for kind, rows in norm.items()
    for row in rows
    for k, v in row.items()
    if k in {"size", "entry_price", "mark_price", "liquidation_price", "unrealized_pnl", "funding_rate", "leverage", "delta",
             "gamma", "vega", "theta", "price", "trigger_price", "filled_size", "remaining_size", "total", "strike"}
    and v is not None and not (isinstance(v, str) and DECIMAL.match(v))
]
check("futures: every number is a decimal string", bad == [])

# --- USD-M futures: what fails, fails alone ---------------------------------------

got = run({**FUTURES, "/fapi/v1/openAlgoOrders": (401, {"code": -2014, "msg": "API-key format invalid."})})
o = got["out"] or {}
check("an algo read the credential's routes do not cover: exit 0, the rest kept", got["exit"] == 0 and o.get("positions"))
check("... and _errors says to enrol again", "enrol the key pair again" in (o.get("_errors") or {}).get("open_algo_orders", ""))
check("... and the regular orders are still rows",
      sorted(x["order_id"] for x in (o.get("_normalized") or {}).get("open_orders", [])) == ["8389765", "8389766", "8389767"])

got = run({**FUTURES, "/fapi/v1/openOrders": (500, {"code": -1000, "msg": "An unknown error occurred"})})
o = got["out"] or {}
check("a failed open_orders read: in _errors, its rows left out (posman keeps the last)",
      got["exit"] == 0 and "open_orders" in (o.get("_errors") or {}) and "open_orders" not in (o.get("_normalized") or {}))
check("... positions and balances still mapped", len((o.get("_normalized") or {}).get("positions", [])) == 5)

got = run({**FUTURES, "/fapi/v1/exchangeInfo": (502, "bad gateway")})
o = got["out"] or {}
n = o.get("_normalized") or {}
check("no exchange info: positions and orders unmapped this fetch, named in _errors",
      got["exit"] == 0 and "exchange_info" in (o.get("_errors") or {}) and "positions" not in n and "open_orders" not in n)
check("... the raw datasets and the balances still there", o.get("positions") and n.get("balances"))

got = run({**FUTURES, "/fapi/v3/positionRisk": (401, {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."})})
check("Binance refusing the key (-2015): exit 3, nothing printed", got["exit"] == 3 and got["out"] is None and "-2015" in got["err"])

got = run({**FUTURES, "/fapi/v3/account": (403, "credential proxy: placeholder not found in the query\n")})
check("the proxy refusing (403, plain text): exit 3", got["exit"] == 3 and "credential proxy" in got["err"])

got = run({**FUTURES, "/fapi/v3/positionRisk": (400, {"code": -1022, "msg": "Signature for this request is not valid."})})
check("a signature that does not verify (-1022): exit 3", got["exit"] == 3)

restricted = (451, {"code": 0, "msg": "Service unavailable from a restricted location"})
got = run({p: restricted for p in FUTURES})
check("nothing readable (451 everywhere): exit 1, the location named", got["exit"] == 1 and "location" in got["err"])

got = run({**FUTURES, "/fapi/v1/openOrders": (429, {"code": -1003, "msg": "Too many requests"})})
check("a rate limit fails that dataset, not the run",
      got["exit"] == 0 and "rate limited" in ((got["out"] or {}).get("_errors") or {}).get("open_orders", ""))

empty = {**FUTURES, "/fapi/v3/positionRisk": (200, [CLOSED]), "/fapi/v1/openOrders": (200, []),
         "/fapi/v1/openAlgoOrders": (200, [])}
got = run(empty)
n = (got["out"] or {}).get("_normalized") or {}
check("nothing open: [] rows, and no public lookups", n.get("positions") == [] and n.get("open_orders") == []
      and not any(r["path"] in ("/fapi/v1/exchangeInfo", "/fapi/v1/premiumIndex", "/fapi/v1/symbolConfig") for r in got["seen"]))

# --- spot ---------------------------------------------------------------------

SPOT_ACCOUNT = {
    "makerCommission": 10, "takerCommission": 10, "canTrade": True, "canWithdraw": True, "canDeposit": True,
    "updateTime": 1762329600000, "accountType": "SPOT", "permissions": ["SPOT"], "uid": 354937868,
    "balances": [
        {"asset": "BTC", "free": "0.10000000", "locked": "0.05000000"},
        {"asset": "USDT", "free": "1000.00000000", "locked": "0.00000000"},
        {"asset": "LTC", "free": "0.00000000", "locked": "0.00000000"},
    ],
}
SPOT_LIMIT = {
    "symbol": "BTCUSDT", "orderId": 28, "orderListId": -1, "clientOrderId": "spot-1", "price": "60000.00000000",
    "origQty": "0.05000000", "executedQty": "0.00000000", "cummulativeQuoteQty": "0.00000000", "status": "NEW",
    "timeInForce": "GTC", "type": "LIMIT", "side": "SELL", "stopPrice": "0.00000000", "icebergQty": "0.00000000",
    "time": 1762329600000, "updateTime": 1762329600000, "isWorking": True, "workingTime": 1762329600000,
    "origQuoteOrderQty": "0.00000000", "selfTradePreventionMode": "EXPIRE_MAKER",
}
SPOT_STOP = dict(SPOT_LIMIT, orderId=29, symbol="ETHUSDT", type="STOP_LOSS_LIMIT", price="3000.00000000",
                 stopPrice="3050.00000000", isWorking=False, origQty="1.00000000")
SPOT_MAKER = dict(SPOT_LIMIT, orderId=30, type="LIMIT_MAKER", side="BUY", price="55000.00000000",
                  status="PARTIALLY_FILLED", executedQty="0.01000000")
SPOT = {
    "/api/v3/account": (200, SPOT_ACCOUNT),
    "/api/v3/openOrders": (200, [SPOT_LIMIT, SPOT_STOP, SPOT_MAKER]),
    "/api/v3/exchangeInfo": (200, {"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"},
        {"symbol": "ETHUSDT", "baseAsset": "ETH", "quoteAsset": "USDT"},
    ]}),
}

got = run(SPOT, cred="CRED_BINANCE_SPOT_SECRET")
check("spot: the run exits 0", got["exit"] == 0)
out = got["out"] or {}
seen = got["seen"]
check("spot: reads the spot endpoints, and only those", sorted(r["path"] for r in seen) == sorted(SPOT))
acct_req = next((r for r in seen if r["path"] == "/api/v3/account"), {"query": "", "headers": {}})
check("spot: asks for non-zero balances only", "omitZeroBalances=true" in acct_req["query"])
check("spot: the signing header is named for the spot credential", "X-Dime-Sign-binance-spot-secret" in acct_req["headers"])
for r in [r for r in seen if signed(r)]:
    check(f"spot signed {r['path']}: is a route of the signing credential", any(matches(p, r["path"]) for p in ROUTES))
info_req = next((r for r in seen if r["path"] == "/api/v3/exchangeInfo"), {"query": ""})
check("spot: exchange info only for the order's symbols, unsigned",
      json.loads(urllib.parse.parse_qs(info_req["query"]).get("symbols", ["[]"])[0]) == ["BTCUSDT", "ETHUSDT"]
      and not signed(info_req))
check("spot: balances are Binance's, a zero one left out", out.get("balances") == SPOT_ACCOUNT["balances"][:2])
check("spot: account is the rest of the answer", out.get("account") == {k: v for k, v in SPOT_ACCOUNT.items() if k != "balances"})
check("spot: open_orders are Binance's records, unchanged", out.get("open_orders") == [SPOT_LIMIT, SPOT_STOP, SPOT_MAKER])
n = out.get("_normalized") or {}
check("spot: holdings are balances, so no positions", n.get("positions") == [])
check("spot: a balance is free plus locked",
      n.get("balances") == [{"asset": "BTC", "total": "0.15"}, {"asset": "USDT", "total": "1000"}])
so = {o["order_id"]: o for o in n.get("open_orders", [])}
check("spot: a resting limit order",
      {k: so.get("28", {}).get(k) for k in ("kind", "underlying", "quote_asset", "contract", "type", "status", "price", "size", "remaining_size", "time_in_force", "side")}
      == {"kind": "spot", "underlying": "BTC", "quote_asset": "USDT", "contract": None, "type": "limit", "status": "open",
          "price": "60000.00000000", "size": "0.05000000", "remaining_size": "0.05", "time_in_force": "gtc", "side": "sell"})
check("spot: a stop-loss limit not yet working is untriggered",
      (so.get("29", {}).get("type"), so.get("29", {}).get("status"), so.get("29", {}).get("trigger_price"))
      == ("stop_limit", "untriggered", "3050.00000000"))
check("spot: LIMIT_MAKER is a post-only limit, partly filled",
      (so.get("30", {}).get("type"), so.get("30", {}).get("time_in_force"), so.get("30", {}).get("status"))
      == ("limit", "post_only", "partially_filled"))

got = run({**SPOT, "/api/v3/openOrders": (401, {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."})},
          cred="CRED_BINANCE_SPOT_SECRET")
check("spot: a refused key is exit 3", got["exit"] == 3)

# --- the environment ------------------------------------------------------------

got = run(FUTURES, cred="CRED_BINANCE_USDM_DEMO_SECRET")
check("usdm-demo reads futures", got["exit"] == 0 and any(r["path"] == "/fapi/v3/positionRisk" for r in got["seen"]))
got = run(SPOT, cred="CRED_BINANCE_SPOT_DEMO_SECRET")
check("spot-demo reads spot", got["exit"] == 0 and any(r["path"] == "/api/v3/account" for r in got["seen"]))
got = run(SPOT, cred="CRED_BINANCE_MAIN_SECRET")
check("a label naming no environment: exit 2, ENVIRONMENT suggested", got["exit"] == 2 and "ENVIRONMENT" in got["err"])
got = run(SPOT, cred="CRED_BINANCE_MAIN_SECRET", params={"ENVIRONMENT": "spot"})
check("ENVIRONMENT overrides the label", got["exit"] == 0 and any(r["path"] == "/api/v3/account" for r in got["seen"]))
got = run(SPOT, cred="CRED_BINANCE_SPOT_SECRET", params={"ENVIRONMENT": "margin"})
check("an ENVIRONMENT that is not one: exit 2", got["exit"] == 2)
got = run(SPOT, cred=None)
check("no credential: exit 2", got["exit"] == 2 and "no credential" in got["err"])

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
