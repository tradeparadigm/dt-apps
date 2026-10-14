"""The Paradex data feed client: the SBE decoder, the stream, the REST helpers.

The decoder is driven by node over frames captured from Paradex's live
WebSocket (sbe_frames.json), and one of them is also read here, in Python,
straight from the bytes, so the check does not rest on the decoder agreeing
with itself. The stream runs against a local WebSocket that answers as
Paradex does: JSON-RPC acks and refusals as text, market data as binary SBE.
Nothing here reaches Paradex. Node 22 or later (a global WebSocket).
"""

import base64
import hashlib
import http.server
import json
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse

LIB = pathlib.Path(__file__).resolve().parents[4] / "apps/paradex/skills/paradex-api/datafeeds/lib"
FRAMES = json.loads((pathlib.Path(__file__).parent / "sbe_frames.json").read_text())["frames"]

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def frame(name):
    return base64.b64decode(FRAMES[name])


# ---- The decoder ----

def variants():
    """Frames as other schema versions and future changes would send them."""
    ms = frame("markets_summary")
    block_len, tpl, schema_id, _ = struct.unpack_from("<HHHH", ms)
    # Version 0: a 216-byte block, without forwardRate, riskFreeRate, fundingRatePrecise.
    v0 = struct.pack("<HHHH", 216, tpl, schema_id, 0) + ms[8:8 + 216] + ms[8 + block_len:]
    # A later version that appends 8 bytes to the block: unknown, skipped.
    longer = struct.pack("<HHHH", block_len + 8, tpl, schema_id, 2) + ms[8:8 + block_len] + b"\x01" * 8 \
        + ms[8 + block_len:]
    trade = frame("trade")
    # The trade without its trailing tradeIdStr, as a 1:2 frame without the in-place addition.
    tb = struct.unpack_from("<H", trade)[0]
    market_len = trade[8 + tb]
    short_trade = trade[:8 + tb + 1 + market_len]
    unknown = struct.pack("<HHHH", 0, 99, schema_id, 2)
    other_schema = struct.pack("<HHHH", block_len, tpl, 7, 2) + ms[8:]
    odd_side = bytearray(trade)
    odd_side[8 + 24] = 9  # side: ts, seq, tradeId come first
    return {
        "v0": v0, "longer": longer, "short_trade": short_trade, "unknown": unknown,
        "other_schema": other_schema, "odd_side": bytes(odd_side),
    }


DECODE = r"""
import fs from "node:fs";
import { parseSchema, decode } from __SBE__;
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const schema = parseSchema(fs.readFileSync(__XML__, "utf8"));
const out = { id: schema.id, version: schema.version, messages: schema.messages.size, blocks: {} };
for (const [id, m] of schema.messages) {
  const last = m.fields.at(-1);
  out.blocks[m.name] = last ? last.offset + last.def.size : 0;
}
out.decoded = {};
for (const [name, b64] of Object.entries(input)) {
  try {
    out.decoded[name] = decode(schema, Buffer.from(b64, "base64"));
  } catch (err) {
    out.decoded[name] = { error: err.message };
  }
}
console.log(JSON.stringify(out));
"""

script = DECODE.replace("__SBE__", json.dumps((LIB / "sbe.mjs").as_uri())).replace(
    "__XML__", json.dumps(str(LIB / "paradex_1_0.xml")))
inputs = dict(FRAMES)
inputs.update({k: base64.b64encode(v).decode() for k, v in variants().items()})
proc = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(inputs),
                      capture_output=True, text=True, timeout=60)
if proc.returncode != 0:
    print(proc.stderr)
    raise SystemExit("decoder harness failed")
r = json.loads(proc.stdout)
d = r["decoded"]

check("the schema is Paradex's 1:2, every message read", (r["id"], r["version"], r["messages"]) == (1, 2, 12))
for name, msg in (("trade", "TradeEvent"), ("bbo", "BboEvent"), ("book", "BookEvent"),
                  ("markets_summary", "MarketSummaryEvent"), ("funding_data", "FundingDataEvent")):
    got = struct.unpack_from("<H", frame(name))[0]
    check(f"{msg}'s layout from the schema is the live frame's block, {got} bytes", r["blocks"][msg] == got)
    check(f"{msg} decodes", d[name].get("message") == msg)

# Read in Python from the bytes, independently of the decoder.
ms = frame("markets_summary")
block_len = struct.unpack_from("<H", ms)[0]
ts, seq, mark = struct.unpack_from("<qqq", ms, 8)
(funding_mantissa,) = struct.unpack_from("<q", ms, 8 + 7 * 8)
(precise_mantissa,) = struct.unpack_from("<q", ms, 8 + 29 * 8)
market = ms[8 + block_len + 1:8 + block_len + 1 + ms[8 + block_len]].decode()
m = d["markets_summary"]
check("a timestamp is microseconds, as on the wire", m["ts"] == ts)
check("a price is its mantissa at -8", abs(m["markPrice"] - mark / 1e8) < 1e-9)
check("a rate is its mantissa at -8", abs(m["fundingRate"] - funding_mantissa / 1e8) < 1e-15)
check("the precise funding rate is its mantissa at -12", abs(m["fundingRatePrecise"] - precise_mantissa / 1e12) < 1e-18)
check("the market comes from the variable-length data after the block", m["market"] == market == "ETH-USD-PERP")
check("an optional field Paradex left empty is null", m["markIv"] is None and m["futureFundingRate"] is None)

t = d["trade"]
check("an enum is its name", t["side"] in ("BUY", "SELL") and t["tradeType"] == "FILL")
check("a trade id past 2^53 stays exact, as a string", isinstance(t["tradeId"], str) and t["tradeIdStr"].isdigit())
book = d["book"]
check("a repeating group is a list of entries, best first",
      len(book["bids"]) == 15 and book["bids"][0]["price"] > book["bids"][1]["price"]
      and book["asks"][0]["price"] < book["asks"][1]["price"])
check("a null price in a snapshot is null", book["bestBidPrice"] is None)
check("the funding period is a plain integer", d["funding_data"]["fundingPeriodHours"] == 8)

check("a version 0 frame reads without the fields appended since",
      "fundingRatePrecise" not in d["v0"] and "riskFreeRate" not in d["v0"]
      and d["v0"]["fundingRate"] == m["fundingRate"] and d["v0"]["market"] == "ETH-USD-PERP")
check("a longer block than the schema knows is skipped to the data after it",
      d["longer"]["market"] == "ETH-USD-PERP" and d["longer"]["markPrice"] == m["markPrice"])
check("variable-length data absent at the end is absent, not an error",
      "tradeIdStr" not in d["short_trade"] and d["short_trade"]["market"] == t["market"])
check("a message the schema lacks is null, to be skipped", d["unknown"] is None)
check("a frame of another schema is refused", "schema 7" in d["other_schema"].get("error", ""))
check("an enum value the schema does not name is its number", d["odd_side"]["side"] == 9)


# ---- The stream, against a local Paradex ----

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


def ws_frame(data, opcode):
    n = len(data)
    head = bytes([0x80 | opcode])
    head += bytes([n]) if n < 126 else bytes([126]) + n.to_bytes(2, "big") if n < 65536 \
        else bytes([127]) + n.to_bytes(8, "big")
    return head + data


class Paradex:
    """Two connections: the first is dropped after its frames, the second stays."""

    def __init__(self):
        self.connections = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.url = f"ws://127.0.0.1:{self.srv.getsockname()[1]}/v1"
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        for n in range(2):
            conn, _ = self.srv.accept()
            seen = {"request": None, "subscribes": []}
            self.connections.append(seen)
            req = b""
            while b"\r\n\r\n" not in req:
                req += conn.recv(4096)
            seen["request"] = req.split(b"\r\n", 1)[0].decode()
            key = re.search(rb"(?i)sec-websocket-key:\s*(\S+)", req).group(1)
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            for _ in range(3):
                _, payload = read_frame(conn)
                sub = json.loads(payload)
                seen["subscribes"].append(sub)
                channel = sub["params"]["channel"]
                if channel.startswith("orders"):
                    answer = {"jsonrpc": "2.0", "id": sub["id"],
                              "error": {"code": -32600, "message": "invalid subscribe request"}}
                else:
                    answer = {"jsonrpc": "2.0", "id": sub["id"], "result": {"channel": channel}}
                conn.sendall(ws_frame(json.dumps(answer).encode(), 1))
            if n == 0:
                conn.sendall(ws_frame(frame("markets_summary"), 2))
                conn.sendall(ws_frame(struct.pack("<HHHH", 0, 99, 1, 2), 2))  # unknown: skipped
                conn.sendall(ws_frame(json.dumps({"jsonrpc": "2.0", "method": "subscription", "params": {
                    "channel": "options_tape.ALL", "data": {"venue": "DBT", "block_id": "b1"}}}).encode(), 1))
                conn.sendall(bytes([0x88, 0]))  # close
                time.sleep(0.2)
                conn.close()
            else:
                conn.sendall(ws_frame(frame("bbo"), 2))
                self.kept = conn


STREAM = r"""
import { stream } from "./paradex.mjs";
const events = [];
const logs = [];
const s = stream(["markets_summary.ETH-USD-PERP", "options_tape.ALL", "orders.BTC-USD-PERP"], (e) => {
  events.push(e);
  if (events.some((x) => x.type === "bbo")) {
    console.log(JSON.stringify({ events, logs }));
    s.close();
    process.exit(0);
  }
}, { url: process.argv[1], log: (t) => logs.push(t) });
setTimeout(() => { console.log(JSON.stringify({ events, logs, timedOut: true })); process.exit(0); }, 15000);
"""

paradex = Paradex()
with tempfile.TemporaryDirectory() as tmp:
    for f in ("paradex.mjs", "sbe.mjs", "paradex_1_0.xml"):
        shutil.copy(LIB / f, pathlib.Path(tmp) / f)
    proc = subprocess.run(["node", "--input-type=module", "-e", STREAM, paradex.url], cwd=tmp, capture_output=True,
                          text=True, timeout=60)
out = json.loads(proc.stdout or "{}")
events, logs = out.get("events", []), out.get("logs", [])

first = paradex.connections[0] if paradex.connections else {"request": None, "subscribes": []}
check("it connects naming the schema version, which Paradex requires",
      first["request"] == "GET /v1?sbeSchemaId=1&sbeSchemaVersion=2 HTTP/1.1")
check("it subscribes to each channel, one JSON-RPC request apiece",
      [s["params"]["channel"] for s in first["subscribes"]]
      == ["markets_summary.ETH-USD-PERP", "options_tape.ALL", "orders.BTC-USD-PERP"]
      and [s["id"] for s in first["subscribes"]] == [1, 2, 3])
summary = next((e for e in events if e.get("type") == "markets_summary"), {})
check("a binary frame arrives decoded and named by its channel",
      summary.get("message") == "MarketSummaryEvent" and summary.get("market") == "ETH-USD-PERP"
      and isinstance(summary.get("fundingRatePrecise"), float))
tape = next((e for e in events if e.get("type") == "options_tape"), {})
check("a JSON channel arrives as its payload", tape == {"type": "options_tape", "channel": "options_tape.ALL",
                                                         "data": {"venue": "DBT", "block_id": "b1"}})
check("a message the schema lacks never reaches the data feed", len(events) == 3 and not out.get("timedOut"))
check("a refused channel is logged by name", any(l == "orders.BTC-USD-PERP refused: invalid subscribe request"
                                                  for l in logs))
check("a dropped connection is opened again and every channel resubscribed",
      len(paradex.connections) == 2 and len(paradex.connections[1]["subscribes"]) == 3
      and any("reconnecting" in l for l in logs))
check("updates keep coming after the reconnect", any(e.get("type") == "bbo" for e in events))


# ---- REST ----

SUMMARIES = [{"symbol": "BTC-USD-PERP", "funding_rate": "0.0001"},
             {"symbol": "BTC-USD-27JUN26-70000-C", "funding_rate": "0"},
             {"symbol": "ETH-USD-PERP", "funding_rate": "-0.00002"}]
rest_requests = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        rest_requests.append((u.path, q))
        if u.path == "/v1/markets/summary":
            body = {"results": SUMMARIES}
        elif u.path == "/v1/pages":
            body = {"results": [1, 2], "next": "c2"} if "cursor" not in q else {"results": [3], "next": None}
        else:
            self.send_response(429)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}/v1"
REST = r"""
import { perpSummaries, restPages, rest } from __PARADEX__;
const base = process.argv[1];
const out = {};
out.perps = (await perpSummaries({ base })).map((p) => p.symbol);
out.pages = await restPages("/pages", { start_time: 5 }, { base });
try { await rest("/busy", {}, { base }); } catch (err) { out.error = err.message; }
console.log(JSON.stringify(out));
""".replace("__PARADEX__", json.dumps((LIB / "paradex.mjs").as_uri()))
proc = subprocess.run(["node", "--input-type=module", "-e", REST, base], capture_output=True, text=True, timeout=60)
server.shutdown()
rr = json.loads(proc.stdout or "{}")
check("perpSummaries keeps the perps of one read of every market",
      rr.get("perps") == ["BTC-USD-PERP", "ETH-USD-PERP"]
      and ("/v1/markets/summary", {"market": ["ALL"]}) in rest_requests)
check("restPages follows the cursor, which alone names a later page",
      rr.get("pages") == [1, 2, 3] and ("/v1/pages", {"cursor": ["c2"]}) in rest_requests)
check("a refused read names its path and status", rr.get("error") == "/busy: HTTP 429")


# ---- When no connection opens ----

class Refuser(http.server.BaseHTTPRequestHandler):
    """Answers every request 400, the upgrade included: Paradex on a schema version it no longer serves."""

    def do_GET(self):
        self.send_response(400)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


refuser = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Refuser)
threading.Thread(target=refuser.serve_forever, daemon=True).start()
closed = socket.socket()
closed.bind(("127.0.0.1", 0))
closed_port = closed.getsockname()[1]
closed.close()  # nothing listens here now

GIVE_UP = r"""
import { stream } from __PARADEX__;
const run = (url) => new Promise((resolve) => {
  const logs = [];
  stream(["bbo.BTC-USD-PERP"], () => {}, {
    url, giveUpMs: 1500, log: (t) => logs.push(t), onGiveUp: () => resolve(logs),
  });
});
const [refused, unreachable] = await Promise.all([run(process.argv[1]), run(process.argv[2])]);
console.log(JSON.stringify({ refused, unreachable }));
process.exit(0);
""".replace("__PARADEX__", json.dumps((LIB / "paradex.mjs").as_uri()))
proc = subprocess.run(["node", "--input-type=module", "-e", GIVE_UP, f"ws://127.0.0.1:{refuser.server_address[1]}/v1",
                       f"ws://127.0.0.1:{closed_port}/v1"], capture_output=True, text=True, timeout=60)
refuser.shutdown()
g = json.loads(proc.stdout or "{}")
refused, unreachable = g.get("refused", []), g.get("unreachable", [])
check("a socket that never opens is given up on, with the reason, and the data feed exits",
      any("no connection for 2s: Paradex refused the URL (HTTP 400)" in l and "SBE schema 1:2" in l for l in refused)
      and any("exiting so the data feed shows as failing" in l for l in refused))
check("tried again before giving up", sum("reconnecting" in l for l in refused) >= 1)
check("an unreachable host is told apart from a refusal",
      any(f"cannot reach 127.0.0.1:{closed_port}" in l for l in unreachable))

app_version = re.search(r"^version:\s*(\S+)", (LIB.parents[3] / "app.yaml").read_text(), re.M).group(1)
client_version = re.search(r'CLIENT_VERSION = "paradex ([^"]+)"', (LIB / "paradex.mjs").read_text()).group(1)
check("the client names the app version it ships in, so a copy's age shows", client_version == app_version)

for name in failed:
    print(f"FAIL {name}")
print(f"{passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
