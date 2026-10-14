"""The options-monitor datafeed suggestion: its logic, and runs of the script.

monitor.mjs is driven through node with fixed packages, in the shape Paradex's
options tape answered on 2026-10-14. datafeed.mjs runs against a local
stand-in for the tape (HTTP and a WebSocket), with a stand-in for the
notification-feed helper that prints what would be posted. Nothing here
reaches Paradex. Node 22 or later (a global WebSocket).
"""

import base64
import hashlib
import http.server
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
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


OCT16 = 1792137600000  # 2026-10-16T08:00:00Z
OCT30 = 1793347200000  # 2026-10-30T08:00:00Z
MAR27 = 1806048000000  # 2027-03-26T08:00:00Z


def opt(kind, strike, side, size, price_usd, expiry=OCT16, underlying="BTC"):
    return {"instrument": f"{underlying}-{strike}-{kind[0]}", "underlying": underlying, "kind": "OPTION",
            "option_type": kind, "expiry": expiry, "strike": str(strike), "side": side,
            "size": str(size), "price": str(price_usd), "price_usd": str(price_usd)}


def perp(side, size, underlying="BTC"):
    return {"instrument": f"{underlying}-PERPETUAL", "underlying": underlying, "kind": "PERP",
            "option_type": None, "expiry": None, "strike": None, "side": side, "size": str(size),
            "price": "84000", "price_usd": "84000"}


def pkg(block, legs, venue="DBT", underlying="BTC", notional="3500000", route="DIRECT", traded_at=1791958691399,
        **extra):
    p = {"venue": venue, "block_id": block, "route": route, "traded_at": traded_at, "underlying": underlying,
         "quantity": "25", "index_price": "84000", "notional_usd": notional, "complete": True, "revision": 1,
         "removed": False, "venue_strategy": None, "legs": legs}
    p.update(extra)
    return p


IRON_CONDOR = pkg("ic", [opt("PUT", 80000, "BUY", 40, 120), opt("PUT", 83500, "SELL", 40, 600),
                         opt("CALL", 84000, "SELL", 40, 900), opt("CALL", 87000, "BUY", 40, 100)],
                  notional="13400000")
SHAPES = {
    "call": [opt("CALL", 90000, "BUY", 1, 100)],
    "put": [opt("PUT", 80000, "SELL", 1, 100)],
    "call spread": [opt("CALL", 88000, "BUY", 1, 500), opt("CALL", 90000, "SELL", 1, 300)],
    "put calendar": [opt("PUT", 80000, "BUY", 1, 500), opt("PUT", 80000, "SELL", 1, 300, expiry=OCT30)],
    "call diagonal": [opt("CALL", 83000, "BUY", 15, 500), opt("CALL", 84000, "SELL", 15, 300, expiry=MAR27)],
    "straddle": [opt("CALL", 84000, "BUY", 1, 500), opt("PUT", 84000, "BUY", 1, 500)],
    "strangle": [opt("CALL", 90000, "SELL", 1, 500), opt("PUT", 78000, "SELL", 1, 500)],
    "risk reversal": [opt("CALL", 90000, "BUY", 1, 500), opt("PUT", 78000, "SELL", 1, 500)],
    "call butterfly": [opt("CALL", 3200, "BUY", 100, 50), opt("CALL", 3500, "SELL", 200, 20),
                       opt("CALL", 3700, "BUY", 100, 10)],
    "put condor": [opt("PUT", k, s, 1, 10) for k, s in ((70000, "BUY"), (72000, "SELL"), (74000, "SELL"),
                                                        (76000, "BUY"))],
    "iron condor": IRON_CONDOR["legs"],
    "iron butterfly": [opt("PUT", 80000, "BUY", 1, 10), opt("PUT", 84000, "SELL", 1, 10),
                       opt("CALL", 84000, "SELL", 1, 10), opt("CALL", 88000, "BUY", 1, 10)],
    "custom": [opt("CALL", 65000, "BUY", 20, 10), opt("PUT", 65000, "SELL", 20, 10),
               opt("CALL", 84000, "SELL", 20, 10, expiry=MAR27), opt("PUT", 84000, "BUY", 20, 10, expiry=MAR27)],
    "call + hedge": [opt("CALL", 90000, "BUY", 1, 100), perp("SELL", 0.5)],
    "uneven butterfly is custom": [opt("CALL", 3200, "BUY", 100, 50), opt("CALL", 3500, "SELL", 100, 20),
                                   opt("CALL", 3700, "BUY", 100, 10)],
}

HARNESS = r"""
import * as m from __MONITOR__;
const input = JSON.parse(await new Promise((ok) => { let s = ""; process.stdin.on("data", (d) => (s += d)).on("end", () => ok(s)); }));
const out = {};
out.shapes = Object.fromEntries(Object.entries(input.shapes).map(([k, legs]) => [k, m.strategyName(legs)]));
out.ironCondor = m.describe(input.ironCondor);
out.hedged = m.describe(input.hedged);
out.hedgeHidden = m.describe(input.hedged, { showHedge: false });
out.many = m.describe(input.many);
out.paradigm = m.describe({ ...input.ironCondor, route: "PARADIGM", venue: "PRDX" });
out.unknownVenue = m.describe({ ...input.ironCondor, venue: "NEWX", notional_usd: null });

// Once per package.
const seen = m.createSeen(2);
const p = input.ironCondor;
out.take = {
  incomplete: m.takeNew(seen, { ...p, complete: false }),
  completed: m.takeNew(seen, { ...p, revision: 2 }),
  again: m.takeNew(seen, p),
  revised: m.takeNew(seen, { ...p, revision: 3 }),
  otherVenue: m.takeNew(seen, { ...p, venue: "DRV" }),
  removed: m.takeNew(seen, { ...p, block_id: "gone", removed: true }),
  noLegs: m.takeNew(seen, { ...p, block_id: "empty", legs: [] }),
  noId: m.takeNew(seen, { ...p, block_id: undefined }),
  third: m.takeNew(seen, { ...p, block_id: "third" }),
  // Two remembered: the first is forgotten once a third is taken.
  firstForgotten: m.takeNew(seen, p),
};

const d = { venue: "DBT", underlying: "BTC", premium: -51200, notional: 13.4e6 };
out.passes = {
  all: m.passes(d, {}),
  venueCode: m.passes(d, { venues: ["dbt"] }),
  venueName: m.passes(d, { venues: ["Deribit"] }),
  otherVenue: m.passes(d, { venues: ["Paradex", "OKX"] }),
  underlying: m.passes(d, { underlyings: ["eth", "btc"] }),
  otherUnderlying: m.passes(d, { underlyings: ["ETH"] }),
  smallNotional: m.passes(d, { minNotional: 2e7 }),
  premiumEitherWay: m.passes(d, { minPremium: 50000 }),
  premiumTooSmall: m.passes(d, { minPremium: 60000 }),
  unknownNotional: m.passes({ ...d, notional: null }, { minNotional: 2e7 }),
};
out.fmt = {
  usd: [m.fmtUsd(950), m.fmtUsd(19400), m.fmtUsd(-3.5e6), m.fmtUsd(1.2e9), m.fmtUsd(123456)],
  size: [m.fmtSize(25), m.fmtSize(0.5), m.fmtSize(1.23456), m.fmtSize("3.2050")],
  expiry: [m.fmtExpiry(1792137600000), m.fmtExpiry(0), m.fmtExpiry(null)],
};
console.log(JSON.stringify(out));
"""


def run_monitor():
    url = (DIR / "monitor.mjs").as_uri()
    script = HARNESS.replace("__MONITOR__", json.dumps(url))
    hedged = pkg("h", SHAPES["call + hedge"], notional="84000")
    many = pkg("m", [opt("PUT", k, "BUY", 1, 10) for k in (60000, 61000, 62000, 63000, 64000)] + [perp("SELL", 1)])
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps({"shapes": SHAPES, "ironCondor": IRON_CONDOR, "hedged": hedged, "many": many}),
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        print(proc.stderr)
        raise SystemExit("monitor.mjs harness failed")
    return json.loads(proc.stdout)


r = run_monitor()
for want, got in r["shapes"].items():
    expected = "custom" if want == "uneven butterfly is custom" else want
    check(f"strategy: {want}", got == expected)

IC_LINE = ("Deribit · BTC iron condor · +40 16OCT26 80000P, -40 16OCT26 83500P, -40 16OCT26 84000C, "
           "+40 16OCT26 87000C · receives $51.2K · $13.4M notional")
check("an iron condor is one line, venue and shape first", r["ironCondor"]["line"] == IC_LINE)
check("premium is bought minus sold", r["ironCondor"]["premium"] == -51200)
check("the filters get the venue code, underlying and notional",
      (r["ironCondor"]["venue"], r["ironCondor"]["underlying"], r["ironCondor"]["notional"]) == ("DBT", "BTC", 13400000))
check("a hedge leg is listed after the options",
      r["hedged"]["line"] == "Deribit · BTC call + hedge · +1 16OCT26 90000C, -0.5 PERP · pays $100 · $84K notional")
check("hidden hedges leave the legs and the name",
      r["hedgeHidden"]["line"] == "Deribit · BTC call · +1 16OCT26 90000C · pays $100 · $84K notional")
check("past four legs, the rest is counted, not listed",
      r["many"]["line"].startswith("Deribit · BTC custom + hedge · +1 16OCT26 60000P, +1 16OCT26 61000P, "
                                   "+1 16OCT26 62000P, +1 16OCT26 63000P, +2 more · pays $50"))
check("a Paradigm-brokered block says so", r["paradigm"]["line"].startswith("Paradex · ")
      and r["paradigm"]["line"].endswith(" · via Paradigm"))
check("an unknown venue shows its code, and no notional is left out rather than guessed",
      r["unknownVenue"]["line"].startswith("NEWX · ") and "notional" not in r["unknownVenue"]["line"]
      and r["unknownVenue"]["notional"] is None)

t = r["take"]
check("an incomplete package waits", t["incomplete"] is False)
check("its completing revision posts", t["completed"] is True)
check("a package posts once", t["again"] is False and t["revised"] is False)
check("the key is the venue and the block id", t["otherVenue"] is True)
check("removed, legless and id-less packages never post",
      t["removed"] is False and t["noLegs"] is False and t["noId"] is False)
check("only the newest packages are remembered", t["third"] is True and t["firstForgotten"] is True)

p = r["passes"]
check("no filters pass everything", p["all"] is True)
check("a venue matches by code or name, any case", p["venueCode"] is True and p["venueName"] is True)
check("a venue outside the list is filtered", p["otherVenue"] is False)
check("underlyings match any case", p["underlying"] is True and p["otherUnderlying"] is False)
check("a notional under the minimum is filtered", p["smallNotional"] is False)
check("the premium minimum applies either way", p["premiumEitherWay"] is True and p["premiumTooSmall"] is False)
check("an unknown notional is not filtered", p["unknownNotional"] is True)

f = r["fmt"]
check("usd", f["usd"] == ["$950", "$19.4K", "$3.5M", "$1.2B", "$123K"])
check("size", f["size"] == ["25", "0.5", "1.2346", "3.205"])
check("expiry", f["expiry"] == ["16OCT26", "PERP", "PERP"])


# ---- datafeed.mjs against a local stand-in for the tape ----

STUB_HELPER = """
export async function openDataFeed(name) {
  console.log(`OPEN ${name}`);
  return { post: (line) => console.log(`POST ${name} ${line}`), close: async () => {} };
}
"""


class Tape:
    """The HTTP side: pages by cursor, newest first, as Paradex answers."""

    def __init__(self, pages, status=200):
        self.pages = pages
        self.status = status
        self.requests = []
        tape = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                tape.requests.append((u.path, q))
                if u.path != "/v1/options-tape" or tape.status != 200:
                    self.send_response(404 if u.path != "/v1/options-tape" else tape.status)
                    self.end_headers()
                    return
                cursor = q.get("cursor", [None])[0]
                index = 0 if cursor is None else int(cursor)
                results = tape.pages[index] if index < len(tape.pages) else []
                body = {"results": results, "prev": None,
                        "next": str(index + 1) if index + 1 < len(tape.pages) else None}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


def recv_exact(conn, n):
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def read_frame(conn):
    b1, b2 = recv_exact(conn, 2)
    n = b2 & 0x7F
    if n == 126:
        n = int.from_bytes(recv_exact(conn, 2), "big")
    elif n == 127:
        n = int.from_bytes(recv_exact(conn, 8), "big")
    mask = recv_exact(conn, 4) if b2 & 0x80 else b"\0\0\0\0"
    payload = recv_exact(conn, n)
    return b1 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def text_frame(text):
    data = text.encode()
    n = len(data)
    head = bytes([0x81, n]) if n < 126 else bytes([0x81, 126]) + n.to_bytes(2, "big") if n < 65536 \
        else bytes([0x81, 127]) + n.to_bytes(8, "big")
    return head + data


class Socket:
    """The WebSocket side: one connection, answers the subscribe, then sends `messages`."""

    def __init__(self, messages):
        self.messages = messages
        self.request_line = None
        self.subscribe = None
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.url = f"ws://127.0.0.1:{self.srv.getsockname()[1]}/v1"
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.srv.accept()
        req = b""
        while b"\r\n\r\n" not in req:
            req += conn.recv(4096)
        self.request_line = req.split(b"\r\n", 1)[0].decode()
        key = re.search(rb"(?i)sec-websocket-key:\s*(\S+)", req).group(1)
        accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        _, payload = read_frame(conn)
        self.subscribe = json.loads(payload)
        conn.sendall(text_frame(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"channel": "options_tape.ALL"}})))
        for m in self.messages:
            conn.sendall(text_frame(json.dumps(m)))
        self.conn = conn  # held open until the process is stopped


def datafeed_env(d, extra):
    env = {"PATH": "/usr/bin:/bin:" + str(pathlib.Path(shutil.which("node")).parent),
           "OPENCLAW_SKILLS_DIR": str(pathlib.Path(d) / "skills")}
    env.update(extra)
    return env


def install(d, config):
    work = pathlib.Path(d) / "work"
    work.mkdir()
    for f in ("datafeed.mjs", "monitor.mjs"):
        shutil.copy(DIR / f, work / f)
    for f in ("paradex.mjs", "sbe.mjs", "paradex_1_0.xml"):
        shutil.copy(DIR.parent / "lib" / f, work / f)
    (work / "config.json").write_text(json.dumps({"name": "options-monitor", **config}))
    helper = pathlib.Path(d) / "skills/notification-feed/scripts/feed.mjs"
    helper.parent.mkdir(parents=True)
    helper.write_text(STUB_HELPER)
    return work


def run_once(config, backfill_minutes):
    with tempfile.TemporaryDirectory() as d:
        work = install(d, config)
        env = datafeed_env(d, {"OPTIONS_MONITOR_ONCE": "1"})
        if backfill_minutes is not None:
            env["OPTIONS_MONITOR_BACKFILL_MINUTES"] = str(backfill_minutes)
        return subprocess.run(["node", "datafeed.mjs"], cwd=work, env=env, capture_output=True, text=True,
                              timeout=60)


def posts_of(stdout):
    return [l.split(" ", 2)[2] for l in stdout.splitlines() if l.startswith("POST options-monitor ")]


now = int(time.time() * 1000)
MIN = 60_000
old = pkg("old", SHAPES["call"], traded_at=now - 90 * MIN)  # before a one-hour backfill
a = pkg("a", SHAPES["put"], venue="DRV", underlying="ETH", notional="5000", traded_at=now - 50 * MIN)
b = pkg("b", SHAPES["call spread"], venue="PRDX", notional="167000", traded_at=now - 20 * MIN, route="PARADIGM")
c = pkg("c", IRON_CONDOR["legs"], notional="13400000", traded_at=now - 5 * MIN)
pending = pkg("pending", SHAPES["straddle"], traded_at=now - 4 * MIN, complete=False)
gone = pkg("gone", SHAPES["strangle"], traded_at=now - 3 * MIN, removed=True)

# Newest first, a package repeated across the page boundary.
tape = Tape([[gone, pending, c, b], [b, a, old]])
config = {"api": tape.url, "ws": "ws://127.0.0.1:9/never"}
proc = run_once(config, backfill_minutes=60)
posts = posts_of(proc.stdout)
check("one run exits cleanly", proc.returncode == 0)
check("it opens the data feed by its configured name", "OPEN options-monitor" in proc.stdout)
check("each package posts once, oldest first, and only from the backfill window",
      [l.split(" · ")[0] + " " + l.split(" · ")[1] for l in posts]
      == ["Derive ETH put", "Paradex BTC call spread", "Deribit BTC iron condor"])
check("an incomplete or removed package does not post", not any("straddle" in l or "strangle" in l for l in posts))
reqs = [q for path, q in tape.requests if path == "/v1/options-tape"]
start = int(reqs[0]["start_time"][0]) if reqs and "start_time" in reqs[0] else 0
check("it reads from half an hour before the window, a page of 1000",
      reqs and reqs[0].get("page_size") == ["1000"] and now - 92 * MIN < start < now - 88 * MIN)
check("a later page is its cursor alone", len(reqs) == 2 and reqs[1] == {"cursor": ["1"]})

proc = run_once({**config, "venues": ["Deribit", "prdx"], "minNotional": 100000}, backfill_minutes=60)
check("config.json's filters apply",
      [l.split(" · ")[0] for l in posts_of(proc.stdout)] == ["Paradex", "Deribit"])

proc = run_once(config, backfill_minutes=None)
check("without a backfill, what is on the tape is history", proc.returncode == 0 and posts_of(proc.stdout) == [])

refusing = Tape([], status=429)
proc = run_once({"api": refusing.url}, backfill_minutes=60)
check("a refused read fails the test run, saying so",
      proc.returncode == 1 and "reading the tape failed: /options-tape: HTTP 429" in proc.stderr)


# ---- The socket, with the HTTP read beside it ----

live = pkg("live", SHAPES["risk reversal"], venue="OKX", notional="751000", traded_at=now + 1000)
early = pkg("early", SHAPES["call"], traded_at=now - 10 * MIN)
sock = Socket([
    {"jsonrpc": "2.0", "method": "subscription", "params": {"channel": "options_tape.ALL", "data": live}},
    # The same package again, as the HTTP read will also return it.
    {"jsonrpc": "2.0", "method": "subscription", "params": {"channel": "options_tape.ALL", "data": live}},
    {"jsonrpc": "2.0", "method": "subscription", "params": {"channel": "trades.BTC", "data": early}},
    "not json",
])
both = Tape([[live, early]])

with tempfile.TemporaryDirectory() as d:
    work = install(d, {"api": both.url, "ws": sock.url})
    child = subprocess.Popen(["node", "datafeed.mjs"], cwd=work, env=datafeed_env(d, {}),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(child.stdout), daemon=True)
    reader.start()
    deadline = time.time() + 15
    while time.time() < deadline and not (sock.subscribe and both.requests and posts_of("\n".join(lines))):
        time.sleep(0.1)
    time.sleep(1)  # anything that would post twice has had its chance
    child.terminate()
    child.wait(timeout=10)
    reader.join(timeout=5)
    err = child.stderr.read()

posts = posts_of("".join(lines))
check("it connects through the Paradex client, naming the schema version",
      sock.request_line == "GET /v1?sbeSchemaId=1&sbeSchemaVersion=2 HTTP/1.1")
check("it subscribes to the whole tape",
      sock.subscribe == {"id": 1, "jsonrpc": "2.0", "method": "subscribe", "params": {"channel": "options_tape.ALL"}})
check("it reads the tape over HTTP when it connects", bool(both.requests))
check("a package from the socket posts once, whichever read sees it first",
      posts == ["OKX · BTC risk reversal · -1 16OCT26 78000P, +1 16OCT26 90000C · pays $0 · $751K notional"])
check("another channel, a stray frame and what predates the start do not post",
      not any("call ·" in l for l in posts))
check("it logs that it connected, and nothing per package", "connected" in err and "risk reversal" not in err)

for name in failed:
    print(f"FAIL {name}")
print(f"{passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
