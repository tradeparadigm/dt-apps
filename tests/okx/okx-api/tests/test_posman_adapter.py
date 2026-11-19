"""The posman adapter for OKX (posman/account/fetch.mjs), against a local OKX.

The adapter is run as the posman runner runs it, `node fetch.mjs` with the
runner's environment, pointed at a stand-in for www.okx.com over plain HTTP
(POSMAN_OKX_API_URL, the adapter's one test hook). Every request it makes is
recorded, so the checks are on what OKX and the credential proxy would see:
the signed string in X-Dime-Sign-<label>, the placeholder in OK-ACCESS-SIGN,
and no key or passphrase (the proxy attaches those). Nothing here reaches OKX
or a credential proxy. Standard library only.
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

ADAPTER = pathlib.Path(__file__).resolve().parents[4] / "apps/okx/skills/okx-api/posman/account/fetch.mjs"

CRED = "CRED_OKX_MAINNET_SECRET"
PLACEHOLDER = "sign-okx-mainnet-secret-7Kj2mQx9vBn4rTz8wLpAeF"
SIGN_HEADER = "X-Dime-Sign-okx-mainnet-secret"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

# ---- OKX's records, as the API sends them (trimmed to the fields used, plus a few) ----

POS_LINEAR = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "2", "ccy": "USDT",
    "avgPx": "70000", "markPx": "74000", "upl": "80", "liqPx": "50000.5", "lever": "10",
    "mgnMode": "cross", "fundingFee": "-1.5", "uTime": "1794384000000", "posId": "1",
}
POS_INVERSE = {
    "instType": "SWAP", "instId": "BTC-USD-SWAP", "posSide": "net", "pos": "-50", "ccy": "BTC",
    "avgPx": "81000", "markPx": "80000", "upl": "0.0007716", "liqPx": "", "lever": "5",
    "mgnMode": "isolated", "fundingFee": "0.00001", "uTime": "1794384000000", "posId": "2",
}
POS_HEDGE_SHORT = {
    "instType": "SWAP", "instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "3", "ccy": "USDT",
    "avgPx": "2500", "markPx": "2400", "upl": "30", "liqPx": "3100", "lever": "3",
    "mgnMode": "cross", "fundingFee": "", "uTime": "1794384000000", "posId": "3",
}
POS_FUTURE = {
    "instType": "FUTURES", "instId": "BTC-USD-261127", "posSide": "net", "pos": "100", "ccy": "BTC",
    "avgPx": "74000", "markPx": "75000", "upl": "0.0018", "liqPx": "60000", "lever": "3",
    "mgnMode": "cross", "fundingFee": "", "uTime": "1794384000000", "posId": "4",
}
POS_OPTION = {
    "instType": "OPTION", "instId": "BTC-USD-261225-74000-C", "posSide": "net", "pos": "10", "ccy": "BTC",
    "avgPx": "0.05", "markPx": "0.055", "upl": "0.0005", "liqPx": "", "lever": "",
    "mgnMode": "cross", "deltaBS": "0.0547", "uTime": "1794384000000", "posId": "5",
}
POS_MARGIN_LONG = {
    "instType": "MARGIN", "instId": "BTC-USDT", "posSide": "net", "pos": "0.5", "posCcy": "BTC",
    "ccy": "USDT", "avgPx": "70000", "markPx": "74000", "upl": "2000", "liqPx": "40000",
    "lever": "3", "mgnMode": "isolated", "liab": "-20000", "liabCcy": "USDT", "uTime": "1794384000000",
}
POS_MARGIN_SHORT = {
    "instType": "MARGIN", "instId": "ETH-USDT", "posSide": "net", "pos": "720", "posCcy": "USDT",
    "ccy": "USDT", "avgPx": "2400", "markPx": "2300", "upl": "30", "liqPx": "3500",
    "lever": "3", "mgnMode": "isolated", "liab": "-0.3", "liabCcy": "ETH", "uTime": "1794384000000",
}
POS_EMPTY = {"instType": "MARGIN", "instId": "SOL-USDT", "pos": "0", "posCcy": "SOL", "mgnMode": "isolated"}
POSITIONS = [POS_LINEAR, POS_INVERSE, POS_HEDGE_SHORT, POS_FUTURE, POS_OPTION, POS_MARGIN_LONG, POS_MARGIN_SHORT, POS_EMPTY]

ORD_POST_ONLY = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "ordId": "101", "clOrdId": "mine1", "side": "buy",
    "ordType": "post_only", "px": "60000", "sz": "5", "accFillSz": "1", "state": "partially_filled",
    "reduceOnly": "false", "cTime": "1794380000000", "tgtCcy": "",
}
ORD_INVERSE = {
    "instType": "SWAP", "instId": "BTC-USD-SWAP", "ordId": "102", "clOrdId": "", "side": "sell",
    "ordType": "limit", "px": "100000", "sz": "10", "accFillSz": "0", "state": "live",
    "reduceOnly": "true", "cTime": "1794380000000", "tgtCcy": "",
}
ORD_OPTION = {
    "instType": "OPTION", "instId": "BTC-USD-261225-74000-C", "ordId": "103", "clOrdId": "", "side": "sell",
    "ordType": "limit", "px": "0.06", "pxUsd": "4400", "sz": "5", "accFillSz": "0", "state": "live",
    "reduceOnly": "false", "cTime": "1794380000000", "tgtCcy": "",
}
ORD_SPOT = {
    "instType": "SPOT", "instId": "BTC-USDT", "ordId": "104", "clOrdId": "", "side": "buy",
    "ordType": "limit", "px": "65000", "sz": "0.25", "accFillSz": "0", "state": "live",
    "reduceOnly": "false", "cTime": "1794380000000", "tgtCcy": "",
}
ORD_SPOT_QUOTE = {
    "instType": "SPOT", "instId": "ETH-USDT", "ordId": "105", "side": "buy", "ordType": "market",
    "px": "", "sz": "100", "accFillSz": "0", "state": "live", "tgtCcy": "quote_ccy", "cTime": "1794380000000",
}
ORDERS = [ORD_POST_ONLY, ORD_INVERSE, ORD_OPTION, ORD_SPOT, ORD_SPOT_QUOTE]

ALGO_SL = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "algoId": "a1", "algoClOrdId": "sl1", "ordType": "conditional",
    "side": "sell", "sz": "2", "slTriggerPx": "65000", "slOrdPx": "-1", "tpTriggerPx": "", "state": "live",
    "reduceOnly": "true", "cTime": "1794381000000",
}
ALGO_OCO = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "algoId": "a2", "ordType": "oco", "side": "sell", "sz": "2",
    "slTriggerPx": "65000", "slOrdPx": "-1", "tpTriggerPx": "90000", "tpOrdPx": "-1", "state": "live",
    "reduceOnly": "true", "cTime": "1794381000000",
}
ALGO_WHOLE_POSITION = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "algoId": "a5", "ordType": "conditional", "side": "sell",
    "sz": "", "closeFraction": "1", "slTriggerPx": "60000", "slOrdPx": "-1", "state": "live", "cTime": "1794381000000",
}
ALGO_TRIGGER = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "algoId": "a3", "ordType": "trigger", "side": "buy", "sz": "1",
    "triggerPx": "80000", "ordPx": "79000", "state": "live", "reduceOnly": "false", "cTime": "1794381000000",
}
ALGO_TRAILING = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "algoId": "a4", "ordType": "move_order_stop", "side": "sell",
    "sz": "1", "moveTriggerPx": "71000", "callbackRatio": "0.05", "state": "live", "reduceOnly": True,
    "cTime": "1794381000000",
}
ALGOS = {
    "conditional,oco": [ALGO_SL, ALGO_OCO, ALGO_WHOLE_POSITION],
    "trigger": [ALGO_TRIGGER],
    "move_order_stop": [ALGO_TRAILING],
}

BALANCE = {
    "totalEq": "100000", "adjEq": "99000", "imr": "5000", "mmr": "1000", "mgnRatio": "99", "uTime": "1794384100000",
    "details": [
        {"ccy": "USDT", "cashBal": "5000", "eq": "5080", "uTime": "1794384100000"},
        {"ccy": "BTC", "cashBal": "1.2", "eq": "1.2025", "uTime": "1794384100000"},
        {"ccy": "ETH", "cashBal": "-0.3", "eq": "-0.3", "liab": "0.3", "uTime": "1794384100000"},
    ],
}
CONFIG = {"acctLv": "3", "posMode": "net_mode", "uid": "1"}

INSTRUMENTS = {
    ("SWAP", "BTC-USDT"): [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "uly": "BTC-USDT", "settleCcy": "USDT",
                            "ctVal": "0.01", "ctMult": "1", "ctValCcy": "BTC", "ctType": "linear", "expTime": ""}],
    ("SWAP", "BTC-USD"): [{"instId": "BTC-USD-SWAP", "instType": "SWAP", "uly": "BTC-USD", "settleCcy": "BTC",
                           "ctVal": "100", "ctMult": "1", "ctValCcy": "USD", "ctType": "inverse", "expTime": ""}],
    ("SWAP", "ETH-USDT"): [{"instId": "ETH-USDT-SWAP", "instType": "SWAP", "uly": "ETH-USDT", "settleCcy": "USDT",
                            "ctVal": "0.1", "ctMult": "1", "ctValCcy": "ETH", "ctType": "linear", "expTime": ""}],
    ("FUTURES", "BTC-USD"): [{"instId": "BTC-USD-261127", "instType": "FUTURES", "uly": "BTC-USD", "settleCcy": "BTC",
                              "ctVal": "100", "ctMult": "1", "ctValCcy": "USD", "ctType": "inverse",
                              "expTime": "1795766400000"}],
    ("OPTION", "BTC-USD"): [{"instId": "BTC-USD-261225-74000-C", "instType": "OPTION", "uly": "BTC-USD",
                             "settleCcy": "BTC", "ctVal": "1", "ctMult": "0.01", "ctValCcy": "BTC",
                             "ctType": "inverse", "optType": "C", "stk": "74000", "expTime": "1798185600000"}],
}
FUNDING = [
    {"instId": "BTC-USDT-SWAP", "fundingRate": "0.0001", "fundingTime": "1794412800000", "nextFundingTime": "1794441600000"},
    {"instId": "BTC-USD-SWAP", "fundingRate": "-0.00008", "fundingTime": "1794412800000", "nextFundingTime": "1794441600000"},
    {"instId": "ETH-USDT-SWAP", "fundingRate": "0.0002", "fundingTime": "1794412800000", "nextFundingTime": "1794427200000"},
]
OPT_SUMMARY = [
    {"instId": "BTC-USD-261225-74000-C", "deltaBS": "0.547", "gammaBS": "0.000041", "vegaBS": "101.7",
     "thetaBS": "-49.58", "markVol": "0.376", "delta": "0.49"},
]

SIGNED = ("/api/v5/account/", "/api/v5/trade/")

# ---- the stand-in ----

requests = []
routes = {}  # path -> fn(query) -> (status, body)


def ok(data):
    return 200, {"code": "0", "msg": "", "data": data}


def default_routes():
    return {
        "/api/v5/account/positions": lambda q: ok(POSITIONS),
        "/api/v5/trade/orders-pending": lambda q: ok(ORDERS),
        "/api/v5/trade/orders-algo-pending": lambda q: ok(ALGOS.get(q.get("ordType"), [])),
        "/api/v5/account/balance": lambda q: ok([BALANCE]),
        "/api/v5/account/config": lambda q: ok([CONFIG]),
        "/api/v5/public/instruments": lambda q: ok(INSTRUMENTS.get((q.get("instType"), q.get("instFamily")), [])),
        "/api/v5/public/funding-rate": lambda q: ok(FUNDING) if q.get("instId") == "ANY" else ok([]),
        "/api/v5/public/opt-summary": lambda q: ok(OPT_SUMMARY if q.get("instFamily") == "BTC-USD" else []),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
        requests.append({"path": url.path, "raw": self.path, "query": query, "headers": dict(self.headers)})
        route = routes.get(url.path)
        status, body = route(query) if route else (404, {"code": "404", "msg": "no such path"})
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{server.server_address[1]}"


def run(cred=CRED, params=None, extra=None, override=None):
    requests.clear()
    routes.clear()
    routes.update(default_routes())
    routes.update(override or {})
    env = {k: v for k, v in os.environ.items() if not k.startswith(("POSMAN_", "CRED_"))}
    env.update({"POSMAN_OKX_API_URL": URL, "POSMAN_SOURCE": "okx-main", "POSMAN_VENUE": "okx", "POSMAN_CRED": cred})
    if cred:
        env[cred] = PLACEHOLDER
    for k, v in (params or {}).items():
        env[f"POSMAN_PARAM_{k}"] = v
    env.update(extra or {})
    res = subprocess.run(["node", str(ADAPTER)], cwd=ADAPTER.parent, env=env, capture_output=True, text=True, timeout=60)
    out = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else None
    return res.returncode, out, res.stderr


passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def rows_by(rows, key):
    return {r[key]: r for r in rows}


# ---- one whole fetch ----

code, out, err = run()
check("a fetch exits 0", code == 0)
if code != 0:
    print(err)
out = out or {}

check("positions are OKX's records unchanged", out.get("positions") == POSITIONS)
check("open orders are OKX's records unchanged", out.get("open_orders") == ORDERS)
check("algo orders are every kind's records, joined",
      out.get("algo_orders") == ALGOS["conditional,oco"] + ALGOS["trigger"] + ALGOS["move_order_stop"])
check("balances are the balance answer's details", out.get("balances") == BALANCE["details"])
check("account is the balance answer without its details",
      out.get("account") == {k: v for k, v in BALANCE.items() if k != "details"})
check("account_config is the config record", out.get("account_config") == CONFIG)
check("nothing failed", "_errors" not in out)

# What OKX and the proxy see.
signed = [r for r in requests if r["path"].startswith(SIGNED)]
public = [r for r in requests if r["path"].startswith("/api/v5/public/")]
check("every private dataset was asked for",
      {r["path"] for r in signed} == {"/api/v5/account/positions", "/api/v5/trade/orders-pending",
                                     "/api/v5/trade/orders-algo-pending", "/api/v5/account/balance",
                                     "/api/v5/account/config"})


def signed_right(r):
    h = {k.lower(): v for k, v in r["headers"].items()}
    ts = h.get("ok-access-timestamp", "")
    want = base64.b64encode(f"{ts}GET{r['raw']}".encode()).decode()
    return (TS_RE.match(ts) is not None and h.get("ok-access-sign") == PLACEHOLDER
            and h.get(SIGN_HEADER.lower()) == want)


check("each private request signs timestamp + GET + its path with the query", all(signed_right(r) for r in signed))
check("the signed path is the one sent, query included",
      any(r["raw"] == "/api/v5/trade/orders-algo-pending?ordType=conditional,oco&limit=100" for r in signed))
check("no request carries the key or the passphrase (the proxy attaches them)",
      all("ok-access-key" not in {k.lower() for k in r["headers"]}
          and "ok-access-passphrase" not in {k.lower() for k in r["headers"]} for r in requests))
check("public reads carry no signature, timestamp or placeholder",
      public and all(not ({k.lower() for k in r["headers"]} & {"ok-access-sign", "ok-access-timestamp", SIGN_HEADER.lower()})
                     for r in public))
check("live trading sends no x-simulated-trading",
      all("x-simulated-trading" not in {k.lower() for k in r["headers"]} for r in requests))
check("instruments are read per family, for the families held",
      {(r["query"].get("instType"), r["query"].get("instFamily")) for r in public if r["path"].endswith("instruments")}
      == set(INSTRUMENTS))
check("funding is read once, for every perpetual",
      [r["query"] for r in public if r["path"].endswith("funding-rate")] == [{"instId": "ANY"}])
check("option greeks are read for the option families held",
      [r["query"] for r in public if r["path"].endswith("opt-summary")] == [{"instFamily": "BTC-USD"}])

# posman's rows.
norm = out.get("_normalized", {})
pos = rows_by(norm.get("positions", []), "symbol")
check("each open position is a row (an empty isolated one is not)",
      set(pos) == {"BTC-USDT-SWAP", "BTC-USD-SWAP", "ETH-USDT-SWAP", "BTC-USD-261127",
                   "BTC-USD-261225-74000-C", "BTC-USDT", "ETH-USDT"})
check("a linear perp", pos.get("BTC-USDT-SWAP") == {
    "symbol": "BTC-USDT-SWAP", "kind": "perp", "underlying": "BTC", "quote_asset": "USDT", "settle_asset": "USDT",
    "contract": "linear", "expiry": None, "strike": None, "option_type": None,
    "side": "long", "size": "0.02", "entry_price": "70000", "mark_price": "74000", "liquidation_price": "50000.5",
    "unrealized_pnl": "80", "funding_pnl": "-1.5", "pnl_asset": "USDT", "margin_mode": "cross", "leverage": "10",
    "funding_rate": "0.0000125", "delta": "0.02", "gamma": "0", "vega": "0", "theta": "0", "iv": None,
    "updated_at": "2026-11-11T08:00:00.000Z",
})
inv = pos.get("BTC-USD-SWAP", {})
check("an inverse perp's size is its USD face value over the mark, in BTC",
      inv.get("size") == "-0.0625" and inv.get("side") == "short" and inv.get("contract") == "inverse"
      and inv.get("quote_asset") == "USD" and inv.get("settle_asset") == "BTC" and inv.get("pnl_asset") == "BTC")
check("no liquidation level is null, not 0", inv.get("liquidation_price") is None)
check("a negative funding rate per hour", inv.get("funding_rate") == "-0.00001")
check("isolated margin", inv.get("margin_mode") == "isolated")
eth = pos.get("ETH-USDT-SWAP", {})
check("a long/short-mode short is negative, from posSide",
      eth.get("size") == "-0.3" and eth.get("side") == "short" and eth.get("delta") == "-0.3")
check("a 4-hour funding period divides by 4", eth.get("funding_rate") == "0.00005")
check("an empty fundingFee is null", eth.get("funding_pnl") is None)
fut = pos.get("BTC-USD-261127", {})
check("an inverse future: its expiry, size at the mark, no funding rate",
      fut.get("kind") == "future" and fut.get("expiry") == "2026-11-27T08:00:00.000Z"
      and fut.get("size") == "0.133333333333" and fut.get("funding_rate") is None)
opt = pos.get("BTC-USD-261225-74000-C", {})
check("an option: contracts x ctVal x ctMult, its strike and type",
      opt.get("kind") == "option" and opt.get("size") == "0.1" and opt.get("strike") == "74000"
      and opt.get("option_type") == "call" and opt.get("quote_asset") == "USD"
      and opt.get("expiry") == "2026-12-25T08:00:00.000Z")
check("an option's coin prices are left null", opt.get("entry_price") is None and opt.get("mark_price") is None)
check("an option's greeks are OKX's BS greeks times its size, and its IV",
      opt.get("delta") == "0.0547" and opt.get("gamma") == "0.0000041" and opt.get("vega") == "10.17"
      and opt.get("theta") == "-4.958" and opt.get("iv") == "0.376")
check("an option's PnL is in its coin", opt.get("unrealized_pnl") == "0.0005" and opt.get("pnl_asset") == "BTC")
ml = pos.get("BTC-USDT", {})
check("a margin long is spot, its size pos in the base",
      ml.get("kind") == "spot" and ml.get("size") == "0.5" and ml.get("contract") is None
      and ml.get("quote_asset") == "USDT" and ml.get("delta") == "0.5")
ms = pos.get("ETH-USDT", {})
check("a margin short is the base borrowed", ms.get("size") == "-0.3" and ms.get("side") == "short")

orders = rows_by(norm.get("open_orders", []), "order_id")
check("each order with a size is a row; a quote-sized market order and a whole-position stop are not",
      set(orders) == {"101", "102", "103", "104", "a1", "a2", "a3", "a4"})
check("a post-only partial fill", orders.get("101") == {
    "symbol": "BTC-USDT-SWAP", "kind": "perp", "underlying": "BTC", "quote_asset": "USDT", "settle_asset": "USDT",
    "contract": "linear", "expiry": None, "strike": None, "option_type": None,
    "order_id": "101", "client_order_id": "mine1", "side": "buy", "type": "limit", "venue_type": "post_only",
    "status": "partially_filled", "venue_status": "partially_filled", "price": "60000", "trigger_price": None,
    "size": "0.05", "filled_size": "0.01", "remaining_size": "0.04", "time_in_force": "post_only",
    "reduce_only": False, "created_at": "2026-11-11T06:53:20.000Z", "expires_at": None,
})
check("an inverse order's size at its own price, reduce-only",
      orders.get("102", {}).get("size") == "0.01" and orders["102"].get("reduce_only") is True
      and orders["102"].get("client_order_id") is None and orders["102"].get("status") == "open")
check("an option order is priced in USD (pxUsd)",
      orders.get("103", {}).get("price") == "4400" and orders["103"].get("size") == "0.05")
check("a spot order", orders.get("104", {}).get("kind") == "spot" and orders["104"].get("size") == "0.25")
check("a stop-loss is a stop_market with its trigger",
      orders.get("a1", {}).get("type") == "stop_market" and orders["a1"].get("trigger_price") == "65000"
      and orders["a1"].get("price") is None and orders["a1"].get("status") == "untriggered"
      and orders["a1"].get("client_order_id") == "sl1" and orders["a1"].get("size") == "0.02")
check("an OCO is other", orders.get("a2", {}).get("type") == "other" and orders["a2"].get("trigger_price") is None)
check("a trigger order keeps its trigger and price",
      orders.get("a3", {}).get("type") == "other" and orders["a3"].get("trigger_price") == "80000"
      and orders["a3"].get("price") == "79000")
check("a trailing stop", orders.get("a4", {}).get("type") == "trailing_stop"
      and orders["a4"].get("trigger_price") == "71000" and orders["a4"].get("reduce_only") is True)
check("the unmapped records are named on stderr", "105" in err and "a5" in err)

bal = rows_by(norm.get("balances", []), "asset")
check("balances are each currency's cash balance, a borrowed one negative",
      bal.get("USDT", {}).get("total") == "5000" and bal.get("BTC", {}).get("total") == "1.2"
      and bal.get("ETH", {}).get("total") == "-0.3"
      and bal.get("USDT", {}).get("updated_at") == "2026-11-11T08:01:40.000Z")

# ---- pages ----

page1 = [dict(ORD_SPOT, ordId=str(1000 - i)) for i in range(100)]
page2 = [dict(ORD_SPOT, ordId="1")]


def paged(q):
    return ok(page2 if q.get("after") == "901" else page1)


code, out, _ = run(override={"/api/v5/trade/orders-pending": paged})
asked = [r["query"] for r in requests if r["path"] == "/api/v5/trade/orders-pending"]
check("a full page asks for the next, after its last id",
      code == 0 and len(asked) == 2 and asked[1].get("after") == "901" and len(out["open_orders"]) == 101)

# ---- demo trading ----

code, out, _ = run(params={"ENVIRONMENT": "demo"})
check("demo trading sends x-simulated-trading: 1 on every request",
      code == 0 and requests and all({k.lower(): v for k, v in r["headers"].items()}.get("x-simulated-trading") == "1"
                                     for r in requests))

# ---- portfolio margin ----

code, out, _ = run(override={"/api/v5/account/config": lambda q: ok([dict(CONFIG, acctLv="4")])})
pm = rows_by(out["_normalized"]["positions"], "symbol")
check("a portfolio-margin account's cross positions are portfolio",
      pm["BTC-USDT-SWAP"]["margin_mode"] == "portfolio" and pm["BTC-USD-SWAP"]["margin_mode"] == "isolated")

# ---- a dataset that fails ----

code, out, _ = run(override={"/api/v5/trade/orders-pending": lambda q: (502, b"bad gateway")})
check("a failed dataset is named in _errors, the rest kept",
      code == 0 and "HTTP 502" in out["_errors"].get("open_orders", "") and out.get("positions") == POSITIONS
      and "open_orders" not in out)
check("without every order list, orders are not mapped (their last rows stay)",
      "open_orders" not in out["_normalized"] and "positions" in out["_normalized"])

code, out, _ = run(override={"/api/v5/account/balance": lambda q: (200, {"code": "50011", "msg": "Rate limit reached", "data": []})})
check("an OKX error code in a 200 fails that dataset",
      code == 0 and "50011" in out["_errors"].get("balances", "") and "account" in out["_errors"]
      and "balances" not in out["_normalized"])

code, out, _ = run(override={"/api/v5/public/instruments": lambda q: (500, b"")})
check("without the instruments, positions and orders are not mapped this fetch",
      code == 0 and "instruments" in out["_errors"] and "positions" not in out["_normalized"]
      and "open_orders" not in out["_normalized"] and "balances" in out["_normalized"]
      and out.get("positions") == POSITIONS)

code, out, _ = run(override={"/api/v5/public/opt-summary": lambda q: (500, b"")})
greekless = rows_by(out["_normalized"]["positions"], "symbol")["BTC-USD-261225-74000-C"]
check("without the option summary, an option's greeks are null",
      code == 0 and "option_summary" in out["_errors"] and greekless["delta"] is None and greekless["iv"] is None)

# ---- refusals ----

code, out, err = run(override={"/api/v5/account/positions": lambda q: (401, {"code": "50111", "msg": "Invalid OK-ACCESS-KEY"})})
check("a 401 is exit 3, naming OKX's code", code == 3 and out is None and "50111" in err)

code, _, err = run(override={"/api/v5/account/balance": lambda q: (200, {"code": "50120", "msg": "no permission", "data": []})})
check("a credential code in a 200 is exit 3", code == 3 and "50120" in err)

code, _, err = run(override={"/api/v5/account/config": lambda q: (401, {"code": "50101", "msg": "APIKey does not match current environment."})})
check("a key for the other environment says which parameter to set", code == 3 and "ENVIRONMENT=demo" in err)

code, _, err = run(override={"/api/v5/trade/orders-pending": lambda q: (403, b"DIME credential proxy: placeholder_absent")})
check("the proxy's 403 is exit 3", code == 3 and "placeholder_absent" in err)

# ---- set up wrong ----

code, _, err = run(cred="")
check("no credential is exit 2", code == 2 and "no credential" in err)

code, _, err = run(cred="CRED_MY_OKX_KEY")
check("a label that names no environment is exit 2, saying how to fix it",
      code == 2 and "ENVIRONMENT=" in err and not requests)

code, out, _ = run(cred="CRED_MY_OKX_KEY", params={"ENVIRONMENT": "mainnet"})
check("the ENVIRONMENT parameter settles it", code == 0 and out.get("positions") == POSITIONS
      and all({k.lower(): v for k, v in r["headers"].items()}.get("x-dime-sign-my-okx-key") for r in requests
              if r["path"].startswith(SIGNED)))

server.shutdown()
print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
