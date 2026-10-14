"""The options-monitor datafeed suggestion: its logic, and one run of the script.

monitor.mjs is driven through node with fixed prints. datafeed.mjs runs
once against a local server answering in the shapes Paradex's public OpenAPI
spec gives for /trades, /markets and /markets/summary, with a stand-in for the
notification-feed helper that prints what would be posted. Nothing here
reaches Paradex.
"""

import http.server
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlparse

DIR = pathlib.Path(__file__).resolve().parents[4] / "apps/paradex/skills/paradex-api/datafeeds/options-monitor"

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


JUN27 = 1782547200000  # 2026-06-27T08:00:00Z
MARKETS = [
    {"symbol": "BTC-USD-27JUN26-70000-C", "asset_kind": "OPTION", "base_currency": "BTC",
     "option_type": "CALL", "strike_price": "70000", "expiry_at": JUN27},
    {"symbol": "BTC-USD-27JUN26-80000-C", "asset_kind": "OPTION", "base_currency": "BTC",
     "option_type": "CALL", "strike_price": "80000", "expiry_at": JUN27},
    {"symbol": "BTC-USD-PERP", "asset_kind": "PERP", "base_currency": "BTC"},
    {"symbol": "ETH-USD-2500-P", "asset_kind": "PERP_OPTION", "base_currency": "ETH",
     "option_type": "PUT", "strike_price": "2500", "expiry_at": 0},
]
PUTS = [
    {"symbol": f"BTC-USD-27JUN26-{k}-P", "asset_kind": "OPTION", "base_currency": "BTC",
     "option_type": "PUT", "strike_price": str(k), "expiry_at": JUN27}
    for k in (60000, 61000, 62000, 63000, 64000, 65000)
]
MARKETS += PUTS
SUMMARIES = [
    {"symbol": "BTC-USD-27JUN26-70000-C", "underlying_price": "70000", "mark_iv": "0.54"},
    {"symbol": "BTC-USD-27JUN26-80000-C", "underlying_price": "70000", "mark_iv": "0.54"},
    {"symbol": "ETH-USD-2500-P", "underlying_price": "2600", "mark_iv": "61.2"},
]


def leg(tid, block, market, side, size, price, at=1782000000000):
    return {"id": tid, "block_id": block, "market": market, "side": side, "size": str(size),
            "price": str(price), "created_at": at, "trade_type": "BLOCK_TRADE"}


SPREAD = [
    leg("t1", "b1", "BTC-USD-27JUN26-70000-C", "BUY", 25, 1000),
    leg("t2", "b1", "BTC-USD-27JUN26-80000-C", "SELL", 25, 224),
    leg("t3", "b1", "BTC-USD-PERP", "SELL", 10, 70000),
]
SPREAD_LINE = ("BTC block · +25 27JUN26 70000C, -25 27JUN26 80000C · hedge -10 PERP · "
               "taker pays $19.4K · $3.5M notional · mark IV 54%")

HARNESS = r"""
import * as m from __MONITOR__;
const input = JSON.parse(await new Promise((ok) => { let s = ""; process.stdin.on("data", (d) => (s += d)).on("end", () => ok(s)); }));
const markets = new Map(input.markets.map((x) => [x.symbol, x]));
const summaries = new Map(input.summaries.map((x) => [x.symbol, x]));
const out = {};
const d = (legs, opts) => m.describe({ legs }, markets, summaries, opts);

out.spread = d(input.spread);
out.noHedge = d(input.spread, { showHedge: false });
out.perpsOnly = d([input.spread[2]]);
out.receives = d([{ ...input.spread[0], side: "SELL" }]);
out.merged = d([input.spread[0], { ...input.spread[0], id: "t1b", size: "5", price: "1000" }]);
out.perpOption = d([{ id: "p1", block_id: "b9", market: "ETH-USD-2500-P", side: "BUY", size: "100", price: "40" }]);
out.many = d(input.puts.map((market, i) => ({ id: `x${i}`, block_id: "b5", market, side: "BUY", size: "1", price: "10" })));
out.noSummary = m.describe({ legs: [input.spread[0]] }, markets, new Map());

// Assembly across polls.
const book = m.createBook();
m.addTrades(book, [input.spread[0]], 1);
const afterOne = m.takeSettled(book, 1).length;
m.addTrades(book, input.spread.slice(1), 2);
const stillOpen = m.takeSettled(book, 2).length;
const settled = m.takeSettled(book, 3);
const again = m.addTrades(book, input.spread, 3);
const straggler = m.addTrades(book, [{ ...input.spread[0], id: "late" }], 4);
out.assembly = { afterOne, stillOpen, settled: settled.map((p) => p.legs.length), again, straggler };

// The first poll's packages never come out.
const first = m.createBook();
m.addTrades(first, input.spread, 1);
m.markDone(first);
out.firstPoll = { open: first.open.size, later: m.addTrades(first, [{ ...input.spread[0], id: "t9" }], 2) };

out.passes = {
  all: m.passes({ base: "BTC", premium: 19400, notional: 3.5e6 }, {}),
  otherBase: m.passes({ base: "BTC", premium: 19400, notional: 3.5e6 }, { baseAssets: ["ETH"] }),
  smallNotional: m.passes({ base: "BTC", premium: 19400, notional: 3.5e6 }, { minNotional: 5e6 }),
  smallPremiumEitherWay: m.passes({ base: "BTC", premium: -900, notional: 3.5e6 }, { minPremium: 1000 }),
  unknownNotional: m.passes({ base: "BTC", premium: 19400, notional: null }, { minNotional: 5e6 }),
};
out.fmt = {
  usd: [m.fmtUsd(950), m.fmtUsd(19400), m.fmtUsd(-3.5e6), m.fmtUsd(1.2e9)],
  size: [m.fmtSize(25), m.fmtSize(0.5), m.fmtSize(1.23456)],
  expiry: [m.fmtExpiry(1782547200000), m.fmtExpiry(0)],
  iv: [m.ivPct("0.54"), m.ivPct("54"), m.ivPct(""), m.ivPct("0")],
};
console.log(JSON.stringify(out));
"""


def run_monitor():
    url = (DIR / "monitor.mjs").as_uri()
    script = HARNESS.replace("__MONITOR__", json.dumps(url))
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps({"markets": MARKETS, "summaries": SUMMARIES, "spread": SPREAD,
                          "puts": [m["symbol"] for m in PUTS]}),
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        print(proc.stderr)
        raise SystemExit("monitor.mjs harness failed")
    return json.loads(proc.stdout)


r = run_monitor()
check("a spread with its hedge is one line, important part first", r["spread"]["line"] == SPREAD_LINE)
check("premium is what the taker pays", r["spread"]["premium"] == 19400)
check("notional is the option legs at the underlying's price", r["spread"]["notional"] == 3500000)
check("hedge legs can be left out", "hedge" not in r["noHedge"]["line"])
check("a package with no option leg is not posted", r["perpsOnly"] is None)
check("a taker selling receives the premium", "taker receives $25K" in r["receives"]["line"])
check("legs of one market and side are merged", r["merged"]["line"].startswith("BTC block · +30 27JUN26 70000C"))
check("a perpetual option reads PERP, and an IV already in percent stays so",
      r["perpOption"]["line"] == "ETH block · +100 PERP 2500P · taker pays $4K · $260K notional · mark IV 61%")
check("past four option legs, the rest is counted, not listed",
      r["many"]["line"].startswith("BTC block · +1 27JUN26 60000P, +1 27JUN26 61000P, "
                                   "+1 27JUN26 62000P, +1 27JUN26 63000P, +2 more · "))
check("with no summary, notional and IV are left out rather than guessed",
      r["noSummary"]["notional"] is None and "notional" not in r["noSummary"]["line"]
      and "IV" not in r["noSummary"]["line"])

a = r["assembly"]
check("a package straddling polls waits for a poll that adds nothing", a["afterOne"] == 0 and a["stillOpen"] == 0)
check("then comes out whole", a["settled"] == [3])
check("prints seen before are skipped", a["again"] == 0)
check("a straggler of a posted package is not posted alone", a["straggler"] == 0)
check("what the first poll finds is never posted", r["firstPoll"] == {"open": 0, "later": 0})

p = r["passes"]
check("no filters pass everything", p["all"] is True)
check("an underlying outside the list is filtered", p["otherBase"] is False)
check("a notional under the minimum is filtered", p["smallNotional"] is False)
check("the premium minimum applies either way", p["smallPremiumEitherWay"] is False)
check("an unknown notional is not filtered", p["unknownNotional"] is True)

f = r["fmt"]
check("usd", f["usd"] == ["$950", "$19.4K", "$3.5M", "$1.2B"])
check("size", f["size"] == ["25", "0.5", "1.2346"])
check("expiry", f["expiry"] == ["27JUN26", "PERP"])
check("iv", f["iv"] == [54, 54, None, None])


# ---- One run of datafeed.mjs against a local stand-in for Paradex ----

PAGE_1 = {"results": SPREAD[:2], "next": "c2", "prev": None}
PAGE_2 = {"results": [SPREAD[2], leg("t7", "b2", "BTC-USD-PERP", "BUY", 3, 70000)], "next": None, "prev": None}
requests = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        requests.append((u.path, q))
        if u.path == "/v1/trades":
            body = PAGE_2 if q.get("cursor") == ["c2"] else PAGE_1
        elif u.path == "/v1/markets":
            body = {"results": MARKETS}
        elif u.path == "/v1/markets/summary":
            body = {"results": SUMMARIES}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


STUB_HELPER = """
export async function openDataFeed(name) {
  return { post: (line) => console.log(`POST ${name} ${line}`), close: async () => {} };
}
"""


def run_datafeed(backfill):
    requests.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as d:
            work = pathlib.Path(d) / "work"
            work.mkdir()
            for f in ("datafeed.mjs", "monitor.mjs"):
                shutil.copy(DIR / f, work / f)
            (work / "config.json").write_text(json.dumps({
                "name": "options-monitor",
                "api": f"http://127.0.0.1:{server.server_address[1]}/v1",
            }))
            helper = pathlib.Path(d) / "skills/notification-feed/scripts/feed.mjs"
            helper.parent.mkdir(parents=True)
            helper.write_text(STUB_HELPER)
            env = {"PATH": "/usr/bin:/bin:" + str(pathlib.Path(shutil.which("node")).parent),
                   "OPENCLAW_SKILLS_DIR": str(pathlib.Path(d) / "skills"),
                   "OPTIONS_MONITOR_ONCE": "1"}
            if backfill:
                env["OPTIONS_MONITOR_BACKFILL_MINUTES"] = "60"
            return subprocess.run(["node", "datafeed.mjs"], cwd=work, env=env,
                                  capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()


proc = run_datafeed(backfill=True)
posts = [l for l in proc.stdout.splitlines() if l.startswith("POST ")]
check("one run exits cleanly", proc.returncode == 0)
check("a package across two pages is posted once, whole", posts == [f"POST options-monitor {SPREAD_LINE}"])
paths = [p for p, _ in requests]
first_trades = next(q for p, q in requests if p == "/v1/trades")
check("it asks for block prints only, from a start time",
      first_trades.get("trade_type") == ["BLOCK_TRADE"] and "start_at" in first_trades)
check("it follows the cursor", any(q.get("cursor") == ["c2"] for p, q in requests if p == "/v1/trades"))
check("it reads the markets and the summaries", "/v1/markets" in paths and "/v1/markets/summary" in paths)

proc = run_datafeed(backfill=False)
check("without a backfill, the first poll posts nothing",
      proc.returncode == 0 and not [l for l in proc.stdout.splitlines() if l.startswith("POST ")])

for name in failed:
    print(f"FAIL {name}")
print(f"{passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
