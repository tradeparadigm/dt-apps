// Global Options Monitor: every options block trade on Paradex, Derive,
// Deribit, Bullish and OKX, one line per package, as Paradex's Option Monitor
// (app.paradex.trade/option-monitor) lists them. Public data, no credential.
// monitor.mjs says where the data comes from.
//
// Installed by the agent from Paradex's suggestion: this file, monitor.mjs
// and the client in ../lib (paradex.mjs, sbe.mjs, paradex_1_0.xml) copied
// into the data feed's workspace directory, beside a config.json holding what
// the user chose (DEFAULTS below lists every key).
//
// It listens on the tape's WebSocket and reads the tape over HTTP each time
// it connects and every few minutes, so a package the socket missed (it was
// reconnecting, a venue reported late) still posts. Each package posts once.
//
// Two switches for a test run, both off in a running data feed:
//   OPTIONS_MONITOR_ONCE=1               one read over HTTP, post, exit; no socket
//   OPTIONS_MONITOR_BACKFILL_MINUTES=N   also post what printed in the last N minutes
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { createSeen, describe, passes, takeNew } from "./monitor.mjs";
import { REST_URL, WS_URL, restPages, stream } from "./paradex.mjs";

const DEFAULTS = {
  name: "options-monitor", // the data feed's name, as registered
  venues: [], // e.g. ["Deribit", "PRDX"]: names or codes; empty is every venue
  underlyings: [], // e.g. ["BTC", "ETH"]; empty is every underlying
  minNotional: 0, // USD
  minPremium: 0, // USD, either direction
  showHedge: true, // show a package's perp hedge legs
  pollMinutes: 5, // the HTTP read between connects
  api: REST_URL,
  ws: WS_URL,
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
// After a restart (an agent update, a pause and resume) it posts what printed
// while it was stopped, from the newest package it had seen, but never from
// further back than this: a data feed off for a day does not dump the day.
const RESUME_CAP_MS = 60 * 60_000;
const STATE_KEYS = 1000;

const here = path.dirname(fileURLToPath(import.meta.url));
const log = (text) => process.stderr.write(`[options-monitor] ${text}\n`);
const cfg = { ...DEFAULTS, ...readConfig(path.join(here, "config.json")) };
const ONCE = process.env.OPTIONS_MONITOR_ONCE === "1";
const BACKFILL_MS = Math.max(0, Number(process.env.OPTIONS_MONITOR_BACKFILL_MINUTES ?? 0) || 0) * 60_000;
// Where it got to, so a restart carries on from there. A test run (a dry run
// or a single read) neither reads nor writes it.
const STATE_FILE = path.join(here, ".state.json");
const KEEP_STATE = !ONCE && process.env.FEED_DRY_RUN !== "1";
const saved = KEEP_STATE ? readState() : null;
// Nothing that printed before this posts. On a first start that is the
// start: what was on the tape is history.
const FLOOR =
  saved && BACKFILL_MS === 0
    ? Math.max(saved.newest - LOOKBACK_MS, Date.now() - RESUME_CAP_MS)
    : Date.now() - BACKFILL_MS;

// Image skills are always in openclaw's own folder (setup's
// MANAGED_SKILLS_DIR), whatever the chart sets OPENCLAW_SKILLS_DIR to.
const helper = path.join(os.homedir(), ".openclaw", "skills", "notification-feed", "scripts", "feed.mjs");
const { openDataFeed } = await import(pathToFileURL(helper).href);
const out = await openDataFeed(cfg.name);

const seen = createSeen();
for (const key of saved?.keys ?? []) seen.keys.add(key);
let newest = Math.max(FLOOR, saved?.newest ?? 0);
let dirty = false;
let readFailing = false;

function readConfig(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    if (err.code !== "ENOENT") log(`config.json unreadable, using defaults: ${err.message}`);
    return {};
  }
}

function readState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    if (!Number.isFinite(s?.newest) || !Array.isArray(s?.keys)) return null;
    return { newest: s.newest, keys: s.keys.filter((k) => typeof k === "string") };
  } catch {
    return null; // none yet, or unreadable: start as a first start does
  }
}

/** Write where it got to, after anything new: a few hundred times a day. */
function saveState() {
  if (!KEEP_STATE || !dirty) return;
  dirty = false;
  const tmp = `${STATE_FILE}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify({ newest, keys: [...seen.keys].slice(-STATE_KEYS) }));
    fs.renameSync(tmp, STATE_FILE);
  } catch (err) {
    log(`cannot save where it got to: ${err.message}`);
  }
}

/** Post one package if it is new, complete, recent enough and passes the filters. */
function offer(p) {
  if (!p || typeof p !== "object") return;
  const at = Number(p.traded_at);
  if (Number.isFinite(at) && at < FLOOR) {
    // Marked, so a later revision of it does not post either.
    dirty = takeNew(seen, p) || dirty;
    return;
  }
  if (!takeNew(seen, p)) return;
  dirty = true;
  if (Number.isFinite(at)) newest = Math.max(newest, at);
  let d;
  try {
    d = describe(p, cfg);
  } catch {
    return; // one malformed package is not worth dying, or a log line, for
  }
  if (passes(d, cfg)) out.post(d.line);
}

/** The tape since a little before the newest package seen, oldest first. */
async function readTape() {
  const start = Math.max(0, Math.min(newest, Date.now()) - LOOKBACK_MS);
  const found = await restPages("/options-tape", { start_time: start, page_size: PAGE_SIZE }, { base: cfg.api, maxPages: MAX_PAGES });
  // Newest first on the wire; posting oldest first keeps the feed in order.
  return found.sort((a, b) => (Number(a?.traded_at) || 0) - (Number(b?.traded_at) || 0));
}

async function catchUp() {
  try {
    for (const p of await readTape()) offer(p);
    saveState();
    if (readFailing) log("reading the tape again");
    readFailing = false;
  } catch (err) {
    if (!readFailing) log(`reading the tape failed: ${err.message}`);
    readFailing = true;
  }
}

if (ONCE) {
  await catchUp();
  await out.close();
  process.exitCode = readFailing ? 1 : 0;
} else {
  stream(
    [CHANNEL],
    (event) => {
      if (event.channel !== CHANNEL) return;
      for (const p of Array.isArray(event.data) ? event.data : [event.data]) offer(p);
      saveState();
    },
    // Whatever printed while it was not connected.
    { url: cfg.ws, idleMs: IDLE_MS, onOpen: catchUp, log },
  );
  setInterval(() => void catchUp(), Math.max(1, Number(cfg.pollMinutes) || DEFAULTS.pollMinutes) * 60_000);
}
