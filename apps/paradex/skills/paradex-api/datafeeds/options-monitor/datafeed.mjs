// Global Options Monitor: every options block trade on Paradex, Derive,
// Deribit, Bullish and OKX, one line per package, as Paradex's Option Monitor
// (app.paradex.trade/option-monitor) lists them. Public data, no credential.
// monitor.mjs says where the data comes from.
//
// Installed by the agent from Paradex's suggestion: this file and monitor.mjs
// copied into the data feed's workspace directory, beside a config.json
// holding what the user chose (DEFAULTS below lists every key).
//
// It listens on the tape's WebSocket and reads the tape over HTTP each time
// it connects and every few minutes, so a package the socket missed (it was
// reconnecting, a venue reported late) still posts. Each package posts once.
//
// Two switches for a test run, both off in a running data feed:
//   OPTIONS_MONITOR_ONCE=1               one read over HTTP, post, exit; no socket
//   OPTIONS_MONITOR_BACKFILL_MINUTES=N   also post what printed in the last N minutes
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { createSeen, describe, passes, takeNew } from "./monitor.mjs";

const DEFAULTS = {
  name: "options-monitor", // the data feed's name, as registered
  venues: [], // e.g. ["Deribit", "PRDX"]: names or codes; empty is every venue
  underlyings: [], // e.g. ["BTC", "ETH"]; empty is every underlying
  minNotional: 0, // USD
  minPremium: 0, // USD, either direction
  showHedge: true, // show a package's perp hedge legs
  pollMinutes: 5, // the HTTP read between connects
  api: "https://api.prod.paradex.trade/v1",
  ws: "wss://ws-public.api.prod.paradex.trade/v1?cancel-on-disconnect=false&sbeSchemaId=1&sbeSchemaVersion=3",
};
const CHANNEL = "options_tape.ALL";
// Each HTTP read goes this far behind the newest package seen: a venue can
// report a package minutes after it printed. Packages already posted are
// skipped by venue and block id.
const LOOKBACK_MS = 30 * 60_000;
const PAGE_SIZE = 1000;
const MAX_PAGES = 10;
// The tape can be quiet for a long time, and the server's pings keep the
// socket open meanwhile, so only a long silence reconnects. The HTTP reads
// cover the gap whatever the socket does.
const IDLE_MS = 30 * 60_000;
const STABLE_MS = 30_000;
const CLOSE_GRACE_MS = 10_000;

const here = path.dirname(fileURLToPath(import.meta.url));
const log = (text) => process.stderr.write(`[options-monitor] ${text}\n`);
const cfg = { ...DEFAULTS, ...readConfig(path.join(here, "config.json")) };
const ONCE = process.env.OPTIONS_MONITOR_ONCE === "1";
const BACKFILL_MS = Math.max(0, Number(process.env.OPTIONS_MONITOR_BACKFILL_MINUTES ?? 0) || 0) * 60_000;
// Nothing that printed before this posts: what was on the tape when the data
// feed started is history.
const FLOOR = Date.now() - BACKFILL_MS;

const skills = process.env.OPENCLAW_SKILLS_DIR || path.join(process.env.HOME ?? "", ".openclaw", "skills");
const { openDataFeed } = await import(pathToFileURL(path.join(skills, "notification-feed", "scripts", "feed.mjs")).href);
const out = await openDataFeed(cfg.name);

const seen = createSeen();
let newest = FLOOR;
let readFailing = false;

function readConfig(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    if (err.code !== "ENOENT") log(`config.json unreadable, using defaults: ${err.message}`);
    return {};
  }
}

/** Post one package if it is new, complete, recent enough and passes the filters. */
function offer(p) {
  if (!p || typeof p !== "object") return;
  const at = Number(p.traded_at);
  if (Number.isFinite(at) && at < FLOOR) {
    // Marked, so a later revision of it does not post either.
    takeNew(seen, p);
    return;
  }
  if (!takeNew(seen, p)) return;
  if (Number.isFinite(at)) newest = Math.max(newest, at);
  let d;
  try {
    d = describe(p, cfg);
  } catch {
    return; // one malformed package is not worth dying, or a log line, for
  }
  if (passes(d, cfg)) out.post(d.line);
}

async function getJson(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(20_000) });
  if (!res.ok) throw new Error(`${new URL(url).pathname}: HTTP ${res.status}`);
  return res.json();
}

/** The tape since a little before the newest package seen, oldest first. */
async function readTape() {
  const start = Math.min(newest, Date.now()) - LOOKBACK_MS;
  const found = [];
  let url = `${cfg.api}/options-tape?start_time=${Math.max(0, start)}&page_size=${PAGE_SIZE}`;
  for (let page = 0; page < MAX_PAGES && url; page++) {
    const body = await getJson(url);
    if (!Array.isArray(body?.results)) throw new Error("/options-tape: no results list");
    found.push(...body.results);
    // A later page needs only its cursor, which carries the window.
    url = body.next ? `${cfg.api}/options-tape?cursor=${encodeURIComponent(body.next)}` : null;
  }
  // Newest first on the wire; posting oldest first keeps the feed in order.
  found.sort((a, b) => (Number(a?.traded_at) || 0) - (Number(b?.traded_at) || 0));
  return found;
}

async function catchUp() {
  try {
    for (const p of await readTape()) offer(p);
    if (readFailing) log("reading the tape again");
    readFailing = false;
  } catch (err) {
    if (!readFailing) log(`reading the tape failed: ${err.message}`);
    readFailing = true;
  }
}

function packagesIn(message) {
  if (message?.method !== "subscription" || message.params?.channel !== CHANNEL) return [];
  const data = message.params.data;
  return Array.isArray(data) ? data : [data];
}

function connect(backoffMs = 1_000) {
  const ws = new WebSocket(cfg.ws);
  ws.binaryType = "arraybuffer";
  let openedAt = 0;
  let idleTimer = null;
  let reason = "disconnected";
  let done = false;

  const reconnect = () => {
    if (done) return;
    done = true;
    clearTimeout(idleTimer);
    ws.close();
    const wait = openedAt > 0 && Date.now() - openedAt >= STABLE_MS ? 1_000 : backoffMs;
    log(`${reason}; reconnecting in ${wait / 1000}s`);
    setTimeout(() => connect(Math.min(wait * 2, 60_000)), wait);
  };
  const touch = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      reason = `sent nothing for ${IDLE_MS / 60_000} minutes`;
      ws.close();
      setTimeout(reconnect, CLOSE_GRACE_MS);
    }, IDLE_MS);
  };

  ws.addEventListener("open", () => {
    openedAt = Date.now();
    log("connected");
    touch();
    ws.send(JSON.stringify({ id: 1, jsonrpc: "2.0", method: "subscribe", params: { channel: CHANNEL } }));
    // Whatever printed while it was not connected.
    void catchUp();
  });
  ws.addEventListener("message", (event) => {
    touch();
    let message;
    try {
      message = JSON.parse(typeof event.data === "string" ? event.data : Buffer.from(event.data).toString("utf8"));
    } catch {
      return;
    }
    if (message?.id === 1 && message.error) {
      reason = `subscribe refused: ${JSON.stringify(message.error).slice(0, 200)}`;
      reconnect();
      return;
    }
    for (const p of packagesIn(message)) offer(p);
  });
  // Node's WebSocket fires only `error` for a connection that never opens.
  ws.addEventListener("close", reconnect);
  ws.addEventListener("error", reconnect);
}

if (ONCE) {
  await catchUp();
  await out.close();
  process.exitCode = readFailing ? 1 : 0;
} else {
  connect();
  setInterval(() => void catchUp(), Math.max(1, Number(cfg.pollMinutes) || DEFAULTS.pollMinutes) * 60_000);
}
