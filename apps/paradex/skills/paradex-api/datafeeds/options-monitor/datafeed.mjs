// Global Options Monitor: every options block trade on Paradex (any user's
// filled RFQ package), one line per package, as Paradex's Option Monitor
// lists them. Public data, no credential. monitor.mjs explains the source.
//
// Installed by the agent from Paradex's suggestion: this file and monitor.mjs
// copied into the data feed's workspace directory, beside a config.json
// holding what the user chose (DEFAULTS below lists every key).
//
// Two switches for a test run, both off in a running data feed:
//   OPTIONS_MONITOR_ONCE=1               one poll, post what it found, exit
//   OPTIONS_MONITOR_BACKFILL_MINUTES=N   start N minutes back instead of now
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { addTrades, createBook, describe, markDone, passes, takeSettled } from "./monitor.mjs";

const DEFAULTS = {
  name: "options-monitor", // the data feed's name, as registered
  environment: "mainnet", // or "testnet"
  baseAssets: [], // e.g. ["BTC", "ETH"]; empty is every underlying
  minPremium: 0, // USD, either direction
  minNotional: 0, // USD, option legs' size at the underlying's price
  showHedge: true, // show a package's perp hedge legs
  pollSeconds: 15,
  api: null, // overrides the environment's API base, e.g. for a test server
};
const HOSTS = { mainnet: "api.prod.paradex.trade", testnet: "api.testnet.paradex.trade" };
// Each poll re-reads this far back: a print can be published a little after
// the time it carries. Prints already seen are skipped by id.
const LOOKBACK_MS = 120_000;
const MAX_PAGES = 10;
const MARKETS_TTL_MS = 10 * 60_000;

const here = path.dirname(fileURLToPath(import.meta.url));
const cfg = { ...DEFAULTS, ...readConfig(path.join(here, "config.json")) };
const API = cfg.api ?? `https://${HOSTS[cfg.environment] ?? HOSTS.mainnet}/v1`;
const ONCE = process.env.OPTIONS_MONITOR_ONCE === "1";
const BACKFILL_MS = Math.max(0, Number(process.env.OPTIONS_MONITOR_BACKFILL_MINUTES ?? 0)) * 60_000;

const skills = process.env.OPENCLAW_SKILLS_DIR || path.join(process.env.HOME ?? "", ".openclaw", "skills");
const { openDataFeed } = await import(pathToFileURL(path.join(skills, "notification-feed", "scripts", "feed.mjs")).href);
const out = await openDataFeed(cfg.name);

const log = (text) => process.stderr.write(`[options-monitor] ${text}\n`);
const book = createBook();
let since = Date.now() - BACKFILL_MS;
let pollNo = 0;
let markets = new Map();
let marketsAt = 0;
let failing = false;

function readConfig(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    if (err.code !== "ENOENT") log(`config.json unreadable, using defaults: ${err.message}`);
    return {};
  }
}

async function getJson(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(20_000) });
  if (!res.ok) throw new Error(`${new URL(url).pathname}: HTTP ${res.status}`);
  return res.json();
}

const rows = (body) => (Array.isArray(body?.results) ? body.results : []);

/** Every block print since the last poll, across pages. Answers the newest time seen. */
async function readTrades() {
  let newest = since;
  let url = `${API}/trades?trade_type=BLOCK_TRADE&page_size=1000&start_at=${since - LOOKBACK_MS}`;
  for (let page = 0; page < MAX_PAGES && url; page++) {
    const body = await getJson(url);
    const results = rows(body);
    addTrades(book, results, pollNo);
    for (const t of results) newest = Math.max(newest, Number(t.created_at) || 0);
    // A later page needs only its cursor.
    url = body?.next ? `${API}/trades?cursor=${encodeURIComponent(body.next)}` : null;
  }
  return newest;
}

/** What each market is, refreshed every ten minutes or for a market not seen before. */
async function ensureMarkets(packages) {
  const unknown = packages.some((p) => p.legs.some((t) => !markets.has(t.market)));
  if (!unknown && Date.now() - marketsAt < MARKETS_TTL_MS) return;
  markets = new Map(rows(await getJson(`${API}/markets`)).map((m) => [m.symbol, m]));
  marketsAt = Date.now();
}

async function poll() {
  pollNo += 1;
  since = Math.min(await readTrades(), Date.now());
  if (pollNo === 1 && BACKFILL_MS === 0) {
    // What is already there when it starts was posted before, or is history.
    markDone(book);
    return;
  }
  const ready = takeSettled(book, ONCE ? Infinity : pollNo);
  if (ready.length === 0) return;
  await ensureMarkets(ready);
  const summaries = new Map(rows(await getJson(`${API}/markets/summary?market=ALL`)).map((s) => [s.symbol, s]));
  for (const pkg of ready) {
    const d = describe(pkg, markets, summaries, cfg);
    if (d && passes(d, cfg)) out.post(d.line);
  }
}

async function tick() {
  try {
    await poll();
    if (failing) log("polling again");
    failing = false;
  } catch (err) {
    if (!failing) log(`poll failed: ${err.message}`);
    failing = true;
  }
}

await tick();
if (ONCE) {
  await out.close();
} else {
  const loop = async () => {
    await tick();
    setTimeout(loop, cfg.pollSeconds * 1000);
  };
  setTimeout(loop, cfg.pollSeconds * 1000);
}
