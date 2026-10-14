// The options monitor's logic, with no I/O: assemble Paradex block-trade
// prints into packages and describe each in one line. datafeed.mjs fetches
// and posts.
//
// WHERE THE DATA COMES FROM. Paradex's Option Monitor (its Block Trades tab)
// lists every user's filled RFQ packages. The public `GET /v1/trades` serves
// the same prints with `trade_type=BLOCK_TRADE`: one print per leg, every leg
// of a package carrying the same `block_id`, ordered by fill id, so a package
// can straddle a page or a poll. The public WebSocket sends one message per
// leg in its binary encoding without `block_id`, so it cannot assemble a
// package on its own. Prints carry no IV, premium or notional; those come
// from `/v1/markets` (what each leg is) and `/v1/markets/summary` (mark IV,
// underlying price).

export const OPTION_KINDS = new Set(["OPTION", "PERP_OPTION"]);
const MAX_OPTION_LEGS_SHOWN = 4;
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** Whether a market (a `/v1/markets` row) is an option. */
export function isOption(meta) {
  return meta != null && (OPTION_KINDS.has(meta.asset_kind) || meta.option_type === "CALL" || meta.option_type === "PUT");
}

/**
 * Packages being assembled, by block_id, and what was already seen. Both sets
 * keep only the newest `remember` entries.
 */
export function createBook({ remember = 5000 } = {}) {
  return { open: new Map(), seenTrades: new Set(), doneBlocks: new Set(), remember };
}

function keep(set, value, limit) {
  set.add(value);
  if (set.size > limit) set.delete(set.values().next().value);
}

/**
 * Add prints from one poll (`pollNo`). A print without a block_id, one seen
 * before (the polls overlap), or one of a package already posted is skipped.
 * Answers how many were new.
 */
export function addTrades(book, trades, pollNo) {
  let added = 0;
  for (const t of trades) {
    if (!t || typeof t.id !== "string" || typeof t.block_id !== "string" || t.block_id === "") continue;
    if (book.seenTrades.has(t.id) || book.doneBlocks.has(t.block_id)) continue;
    keep(book.seenTrades, t.id, book.remember);
    let pkg = book.open.get(t.block_id);
    if (!pkg) {
      pkg = { blockId: t.block_id, legs: [], firstAt: Number(t.created_at) || 0, lastPoll: pollNo };
      book.open.set(t.block_id, pkg);
    }
    pkg.legs.push(t);
    pkg.lastPoll = pollNo;
    added++;
  }
  return added;
}

/**
 * The packages a whole poll (`pollNo`) went by without extending: their legs
 * are all in. They leave the book, oldest first. `Infinity` takes every one.
 */
export function takeSettled(book, pollNo) {
  const out = [];
  for (const [id, pkg] of book.open) {
    if (pkg.lastPoll >= pollNo) continue;
    book.open.delete(id);
    keep(book.doneBlocks, id, book.remember);
    out.push(pkg);
  }
  return out.sort((a, b) => a.firstAt - b.firstAt);
}

/** Drop what is open without posting it: what the first poll finds already there. */
export function markDone(book) {
  for (const id of book.open.keys()) keep(book.doneBlocks, id, book.remember);
  book.open.clear();
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Legs of one market and side, merged (a package may fill one leg in pieces). */
function mergeLegs(legs) {
  const by = new Map();
  for (const t of legs) {
    const size = num(t.size);
    const price = num(t.price);
    if (size == null || price == null) continue;
    const key = `${t.market}\u0000${t.side}`;
    const m = by.get(key) ?? { market: t.market, side: t.side, size: 0, value: 0 };
    m.size += size;
    m.value += size * price;
    by.set(key, m);
  }
  return [...by.values()].map((m) => ({ ...m, price: m.size > 0 ? m.value / m.size : 0 }));
}

function signed(side, text) {
  return `${side === "SELL" ? "-" : "+"}${text}`;
}

/** 25, 0.5, 1.25: no trailing zeros, at most 4 decimals. */
export function fmtSize(n) {
  return String(Number(n.toFixed(4)));
}

/** $950, $19.4K, $2.4M, $1.1B. */
export function fmtUsd(n) {
  const a = Math.abs(n);
  const s = a >= 1e9 ? `${trim(a / 1e9)}B` : a >= 1e6 ? `${trim(a / 1e6)}M` : a >= 1e3 ? `${trim(a / 1e3)}K` : `${Math.round(a)}`;
  return `$${s}`;
}

function trim(n) {
  return n >= 100 ? String(Math.round(n)) : String(Number(n.toFixed(1)));
}

/** 27JUN26 from a UTC millisecond time; PERP for none (a perpetual option). */
export function fmtExpiry(ms) {
  const n = num(ms);
  if (!n) return "PERP";
  const d = new Date(n);
  return `${String(d.getUTCDate()).padStart(2, "0")}${MONTHS[d.getUTCMonth()]}${String(d.getUTCFullYear()).slice(2)}`;
}

/**
 * An IV as a percentage. Paradex's summary documents `mark_iv` without a unit;
 * a value at most 5 is read as a fraction (0.54 is 54%), anything larger as a
 * percentage already. No volatility a monitor shows is 500%.
 */
export function ivPct(value) {
  const n = num(value);
  if (n == null || n <= 0) return null;
  return n <= 5 ? n * 100 : n;
}

function optionLabel(meta, market) {
  const strike = num(meta.strike_price);
  const cp = meta.option_type === "PUT" ? "P" : meta.option_type === "CALL" ? "C" : "";
  if (strike == null || cp === "") return market;
  return `${fmtExpiry(meta.expiry_at)} ${String(strike)}${cp}`;
}

function hedgeLabel(meta, market) {
  return meta?.asset_kind === "PERP" ? "PERP" : market;
}

/**
 * One package as a line, with what the filters read: `{line, base, premium,
 * notional}`, or null when no leg is an option (a package of perps only).
 *
 * + and - are the TAKER's side, as Paradex reports every print: for a block,
 * the side that took the quote. Premium is what the taker pays (positive) or
 * receives (negative) across the option legs; notional is their size at the
 * underlying's price.
 */
export function describe(pkg, markets, summaries, { showHedge = true } = {}) {
  const legs = mergeLegs(pkg.legs).map((l) => ({ ...l, meta: markets.get(l.market) ?? null }));
  const options = legs.filter((l) => isOption(l.meta));
  if (options.length === 0) return null;
  options.sort(
    (a, b) =>
      (num(a.meta.expiry_at) ?? 0) - (num(b.meta.expiry_at) ?? 0) ||
      (num(a.meta.strike_price) ?? 0) - (num(b.meta.strike_price) ?? 0) ||
      a.market.localeCompare(b.market),
  );
  const base = options[0].meta.base_currency || options[0].market.split("-")[0];

  let premium = 0;
  let notional = 0;
  let notionalKnown = true;
  let ivWeight = 0;
  let ivSum = 0;
  for (const l of options) {
    premium += (l.side === "SELL" ? -1 : 1) * l.value;
    const s = summaries.get(l.market);
    const under = num(s?.underlying_price);
    if (under == null) notionalKnown = false;
    else notional += l.size * under;
    const iv = ivPct(s?.mark_iv);
    if (iv != null) {
      ivSum += iv * l.size;
      ivWeight += l.size;
    }
  }

  const shown = options.slice(0, MAX_OPTION_LEGS_SHOWN).map((l) => signed(l.side, `${fmtSize(l.size)} ${optionLabel(l.meta, l.market)}`));
  if (options.length > MAX_OPTION_LEGS_SHOWN) shown.push(`+${options.length - MAX_OPTION_LEGS_SHOWN} more`);
  const parts = [`${base} block`, shown.join(", ")];
  const hedges = legs.filter((l) => !isOption(l.meta));
  if (showHedge && hedges.length > 0) {
    parts.push(`hedge ${hedges.map((l) => signed(l.side, `${fmtSize(l.size)} ${hedgeLabel(l.meta, l.market)}`)).join(", ")}`);
  }
  parts.push(`taker ${premium >= 0 ? "pays" : "receives"} ${fmtUsd(premium)}`);
  if (notionalKnown) parts.push(`${fmtUsd(notional)} notional`);
  if (ivWeight > 0) parts.push(`mark IV ${Math.round(ivSum / ivWeight)}%`);
  return { line: parts.join(" · "), base, premium, notional: notionalKnown ? notional : null };
}

/** The user's filters: underlyings, and minimum premium and notional in USD. */
export function passes(described, { baseAssets = [], minPremium = 0, minNotional = 0 } = {}) {
  if (baseAssets.length > 0 && !baseAssets.includes(described.base)) return false;
  if (Math.abs(described.premium) < minPremium) return false;
  // Unknown notional (no summary for a leg) is not a reason to hide a trade.
  if (described.notional != null && described.notional < minNotional) return false;
  return true;
}
