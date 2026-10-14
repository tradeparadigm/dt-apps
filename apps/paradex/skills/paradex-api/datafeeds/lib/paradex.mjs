// Paradex's public market data for a data feed: the WebSocket (decoded) and
// REST. Copy this folder's three files (paradex.mjs, sbe.mjs, paradex_1_0.xml)
// beside your datafeed.mjs and import from "./paradex.mjs". GUIDE.md, one
// folder up, says which channel to use for what.
//
//   stream(channels, onEvent, options)   live channels, decoded, reconnecting
//   rest(path, query)                    one REST read, JSON
//   restPages(path, query)               a paged REST read, every page's results
//   perpSummaries()                      every perp's market summary, once
//
// Public data only: no key, no signing.
import fs from "node:fs";
import { decode, parseSchema } from "./sbe.mjs";

export const WS_URL = "wss://ws.api.prod.paradex.trade/v1";
export const REST_URL = "https://api.prod.paradex.trade/v1";
// The schema version paradex_1_0.xml describes. Paradex refuses a connection
// that does not name one, and keeps serving old versions when it adds one.
export const SBE_SCHEMA = { id: 1, version: 2 };

// What each binary message is, by the channel it answers.
const CHANNEL_OF = {
  TradeEvent: "trades",
  BboEvent: "bbo",
  BookEvent: "order_book",
  MarketSummaryEvent: "markets_summary",
  FundingDataEvent: "funding_data",
  FundingRateComparisonEvent: "funding_rate_comparison",
  OrderEvent: "orders",
  FillEvent: "fills",
  PositionEvent: "positions",
  AccountEvent: "account",
};

let schema = null;
function loadSchema() {
  schema ??= parseSchema(fs.readFileSync(new URL("./paradex_1_0.xml", import.meta.url), "utf8"));
  return schema;
}

const defaultLog = (text) => process.stderr.write(`[paradex] ${text}\n`);

// A connection that stays up this long starts the backoff over at 1 s; one
// dropped sooner doubles the wait, up to a minute.
const STABLE_MS = 30_000;
const CLOSE_GRACE_MS = 10_000;

/**
 * Subscribe to `channels` (e.g. ["markets_summary.BTC-USD-PERP"]) and call
 * `onEvent(event)` for each update, reconnecting and resubscribing for as long
 * as the process runs.
 *
 * An event from bbo, trades, order_book, markets_summary, funding_data or
 * funding_rate_comparison is one flat object: `type` (the channel's kind,
 * "markets_summary"), `message` (the schema's name), `market`, and the
 * message's fields as GUIDE.md lists them. Prices, sizes and rates are
 * numbers; `ts` and `createdAt` are microseconds. Any other channel (Paradex
 * sends those as JSON) is `{type, channel, data}` with Paradex's payload.
 *
 * Options: `idleMs` (reconnect after this long with no frame; 0 never;
 * default 5 minutes), `onOpen()` (called on each connect, after subscribing:
 * read what you missed over REST there), `log`, `url`.
 * Answers `{subscribe(channel), unsubscribe(channel), close()}`.
 */
export function stream(channels, onEvent, { idleMs = 5 * 60_000, onOpen, log = defaultLog, url = WS_URL } = {}) {
  loadSchema();
  const wanted = new Set(channels);
  const sep = url.includes("?") ? "&" : "?";
  const full = `${url}${sep}sbeSchemaId=${SBE_SCHEMA.id}&sbeSchemaVersion=${SBE_SCHEMA.version}`;
  let ws = null;
  let closed = false;
  let nextId = 1;
  const pending = new Map(); // request id -> channel, to name a refusal

  const send = (method, channel) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const id = nextId++;
    pending.set(id, channel);
    ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params: { channel } }));
  };

  const connect = (backoffMs) => {
    if (closed) return;
    const sock = new WebSocket(full);
    ws = sock;
    sock.binaryType = "arraybuffer";
    let openedAt = 0;
    let idleTimer = null;
    let reason = "disconnected";
    let done = false;

    const reconnect = () => {
      if (done) return;
      done = true;
      clearTimeout(idleTimer);
      sock.close();
      if (closed) return;
      const wait = openedAt > 0 && Date.now() - openedAt >= STABLE_MS ? 1_000 : backoffMs;
      log(`${reason}; reconnecting in ${wait / 1000}s`);
      setTimeout(() => connect(Math.min(wait * 2, 60_000)), wait);
    };
    const touch = () => {
      if (!idleMs) return;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        reason = `sent nothing for ${Math.round(idleMs / 1000)}s`;
        sock.close();
        setTimeout(reconnect, CLOSE_GRACE_MS);
      }, idleMs);
    };

    sock.addEventListener("open", () => {
      openedAt = Date.now();
      log(`connected, ${wanted.size} channel${wanted.size === 1 ? "" : "s"}`);
      touch();
      for (const ch of wanted) send("subscribe", ch);
      if (onOpen) Promise.resolve().then(onOpen).catch((err) => log(`onOpen failed: ${err.message}`));
    });
    sock.addEventListener("message", (msg) => {
      touch();
      let event;
      try {
        event = typeof msg.data === "string" ? fromJson(msg.data, pending, log) : fromBinary(msg.data);
      } catch (err) {
        log(`undecodable frame skipped: ${err.message}`);
        return;
      }
      if (event) onEvent(event);
    });
    sock.addEventListener("close", reconnect);
    sock.addEventListener("error", reconnect);
  };

  connect(1_000);
  return {
    subscribe(channel) {
      if (wanted.has(channel)) return;
      wanted.add(channel);
      send("subscribe", channel);
    },
    unsubscribe(channel) {
      if (!wanted.delete(channel)) return;
      send("unsubscribe", channel);
    },
    close() {
      closed = true;
      ws?.close();
    },
  };
}

function fromBinary(data) {
  const d = decode(loadSchema(), data);
  if (!d || !CHANNEL_OF[d.message]) return null; // a heartbeat, an ack, a message this schema lacks
  return { type: CHANNEL_OF[d.message], ...d };
}

function fromJson(text, pending, log) {
  const m = JSON.parse(text);
  if (m.id !== undefined) {
    const channel = pending.get(m.id);
    pending.delete(m.id);
    if (m.error) log(`${channel ?? `request ${m.id}`} refused: ${m.error.message ?? JSON.stringify(m.error)}`);
    return null;
  }
  if (m.method !== "subscription" || !m.params?.channel) return null;
  return { type: m.params.channel.split(".")[0], channel: m.params.channel, data: m.params.data };
}

/** GET `REST_URL + path` with `query`, as JSON. Throws on a non-2xx answer, naming the path and status. */
export async function rest(path, query = {}, { base = REST_URL, timeoutMs = 30_000 } = {}) {
  const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null)).toString();
  const res = await fetch(`${base}${path}${qs ? `?${qs}` : ""}`, { signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

/** Every page of a paged REST read (`{results, next}`), as one list. A later page is its cursor alone. */
export async function restPages(path, query = {}, { base = REST_URL, maxPages = 10 } = {}) {
  const all = [];
  let body = await rest(path, query, { base });
  for (let page = 1; ; page++) {
    if (!Array.isArray(body?.results)) throw new Error(`${path}: no results list`);
    all.push(...body.results);
    if (!body.next || page >= maxPages) return all;
    body = await rest(path, { cursor: body.next }, { base });
  }
}

/**
 * Every perpetual's market summary (`symbol`, `funding_rate`, `mark_price`,
 * ... as strings, Paradex's REST shape). Paradex filters this read by nothing
 * but one market, so it is every market, options included: about 6.5 MB and
 * 2 s. Call it once at start for the list of perps and where they stand,
 * then follow them on the stream; never on a timer.
 */
export async function perpSummaries({ base = REST_URL } = {}) {
  const body = await rest("/markets/summary", { market: "ALL" }, { base, timeoutMs: 60_000 });
  if (!Array.isArray(body?.results)) throw new Error("/markets/summary: no results list");
  return body.results.filter((m) => typeof m.symbol === "string" && m.symbol.endsWith("-PERP"));
}
