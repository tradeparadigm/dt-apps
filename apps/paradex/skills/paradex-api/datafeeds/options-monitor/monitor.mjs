// The options monitor's logic, with no I/O: which packages to post, and each
// as one line. datafeed.mjs connects and posts.
//
// WHERE THE DATA COMES FROM. Paradex's Global Option Monitor
// (app.paradex.trade/option-monitor) reads its own options tape: block
// trades on option markets across Paradex, Derive, Deribit, Bullish and OKX,
// each one a whole package with its legs. The page reads it from two places,
// and so does this:
//   GET  https://api.prod.paradex.trade/v1/options-tape?start_time=<ms>&page_size=1000
//   WSS  wss://ws-public.api.prod.paradex.trade/v1, channel options_tape.ALL
// Both are public. Neither is in Paradex's published API reference: the shapes
// below are what they answered on 2026-10-14.
//
// A package: {venue, block_id, route, traded_at, underlying, quantity,
// index_price, notional_usd, complete, revision, removed, venue_strategy,
// legs: [{instrument, kind, option_type, expiry, strike, side, size, price,
// price_usd, ...}]}. A leg's `kind` is OPTION or PERP (a delta hedge).

export const VENUES = {
  PRDX: "Paradex",
  DRV: "Derive",
  DBT: "Deribit",
  BLSH: "Bullish",
  OKX: "OKX",
  PARADIGM: "Paradigm",
};
const MAX_LEGS_SHOWN = 4;
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export const packageKey = (p) => `${p.venue}:${p.block_id}`;

/**
 * The packages already posted, keeping only the newest `remember`. A package
 * posts once: a later revision of one already posted (a venue's correction)
 * and one marked removed are not posted again.
 */
export function createSeen(remember = 5000) {
  return { keys: new Set(), remember };
}

/** Whether to post `p`, marking it posted when so. Incomplete ones wait for the revision that completes them. */
export function takeNew(seen, p) {
  if (!p || typeof p.block_id !== "string" || typeof p.venue !== "string") return false;
  if (p.complete !== true || p.removed === true) return false;
  if (!Array.isArray(p.legs) || p.legs.length === 0) return false;
  const key = packageKey(p);
  if (seen.keys.has(key)) return false;
  seen.keys.add(key);
  if (seen.keys.size > seen.remember) seen.keys.delete(seen.keys.values().next().value);
  return true;
}

function num(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** 25, 0.5, 1.25: no trailing zeros, at most 4 decimals. */
export function fmtSize(n) {
  return String(Number(Number(n).toFixed(4)));
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

const isOption = (l) => l.kind === "OPTION" && (l.option_type === "CALL" || l.option_type === "PUT");
const sign = (l) => (l.side === "SELL" ? -1 : 1);

/**
 * A name for the package's shape, read from its option legs: call, put, call
 * or put spread, straddle, strangle, risk reversal, calendar, diagonal,
 * butterfly, condor, iron condor, iron butterfly, else custom. A perp leg adds
 * "+ hedge". The page names the same shapes (CSpd, ICondor, ...); the venue's
 * own label (`venue_strategy`) is too uneven across venues to show.
 */
export function strategyName(legs) {
  const opts = legs.filter(isOption);
  const hedge = legs.some((l) => l.kind === "PERP") ? " + hedge" : "";
  const name = shape(opts);
  return `${name}${hedge}`;
}

function shape(o) {
  const n = o.length;
  if (n === 0) return "custom";
  const calls = o.filter((l) => l.option_type === "CALL").length;
  const puts = n - calls;
  const expiries = new Set(o.map((l) => l.expiry)).size;
  const strikes = new Set(o.map((l) => l.strike)).size;
  const sizes = new Set(o.map((l) => num(l.size))).size;
  const sides = new Set(o.map((l) => l.side)).size;
  const type = calls === n ? "call" : puts === n ? "put" : null;
  if (n === 1) return o[0].option_type === "CALL" ? "call" : "put";
  if (n === 2) {
    const [a, b] = o;
    if (type && expiries === 1 && sides === 2) return `${type} spread`;
    if (type && expiries === 2 && sides === 2) return strikes === 1 ? `${type} calendar` : `${type} diagonal`;
    if (!type && expiries === 1 && sides === 1) return a.strike === b.strike ? "straddle" : "strangle";
    if (!type && expiries === 1 && sides === 2) return "risk reversal";
    return "custom";
  }
  if (n === 3 && type && expiries === 1 && strikes === 3) {
    const sorted = [...o].sort((x, y) => num(x.strike) - num(y.strike));
    const [lo, mid, hi] = sorted;
    if (lo.side === hi.side && mid.side !== lo.side && num(mid.size) === 2 * num(lo.size) && num(lo.size) === num(hi.size)) {
      return `${type} butterfly`;
    }
  }
  if (n === 4 && expiries === 1 && sizes === 1) {
    if (type && strikes === 4) return `${type} condor`;
    if (calls === 2 && puts === 2) return strikes === 3 ? "iron butterfly" : "iron condor";
  }
  return "custom";
}

function legLabel(l) {
  if (!isOption(l)) return `${l.side === "SELL" ? "-" : "+"}${fmtSize(l.size)} ${l.kind === "PERP" ? "PERP" : l.instrument}`;
  const cp = l.option_type === "PUT" ? "P" : "C";
  return `${l.side === "SELL" ? "-" : "+"}${fmtSize(l.size)} ${fmtExpiry(l.expiry)} ${fmtSize(l.strike)}${cp}`;
}

/**
 * One package as a line, with what the filters read: `{line, venue,
 * underlying, premium, notional}`.
 *
 * + and - are each leg's side as the venue reports it. Premium is the option
 * legs' price in USD times size, bought minus sold: what that side pays
 * (positive) or receives (negative).
 */
export function describe(p, { showHedge = true } = {}) {
  const legs = p.legs.filter((l) => showHedge || l.kind !== "PERP");
  const opts = p.legs.filter(isOption).sort(
    (a, b) => (num(a.expiry) ?? 0) - (num(b.expiry) ?? 0) || (num(a.strike) ?? 0) - (num(b.strike) ?? 0) || (a.option_type < b.option_type ? -1 : 1),
  );
  const hedges = legs.filter((l) => !isOption(l));
  let premium = 0;
  for (const l of opts) premium += sign(l) * (num(l.price_usd) ?? 0) * (num(l.size) ?? 0);
  const notional = num(p.notional_usd);
  const venue = VENUES[p.venue] ?? p.venue;
  const shown = [...opts, ...hedges].map(legLabel);
  const legsText = shown.length > MAX_LEGS_SHOWN ? `${shown.slice(0, MAX_LEGS_SHOWN).join(", ")}, +${shown.length - MAX_LEGS_SHOWN} more` : shown.join(", ");
  const parts = [`${venue} · ${p.underlying} ${strategyName(legs)}`, legsText];
  if (opts.length > 0) parts.push(`${premium >= 0 ? "pays" : "receives"} ${fmtUsd(premium)}`);
  if (notional != null) parts.push(`${fmtUsd(notional)} notional`);
  if (p.route === "PARADIGM") parts.push("via Paradigm");
  return { line: parts.join(" · "), venue: p.venue, underlying: p.underlying, premium, notional };
}

/**
 * The user's filters: venues (codes or names), underlyings, and minimum
 * notional and premium in USD (premium either way). Empty lists pass all.
 */
export function passes(d, { venues = [], underlyings = [], minNotional = 0, minPremium = 0 } = {}) {
  if (venues.length > 0) {
    const want = venues.map((v) => String(v).toUpperCase());
    const name = (VENUES[d.venue] ?? d.venue).toUpperCase();
    if (!want.includes(d.venue) && !want.includes(name)) return false;
  }
  if (underlyings.length > 0 && !underlyings.map((u) => String(u).toUpperCase()).includes(d.underlying)) return false;
  if (d.notional != null && d.notional < minNotional) return false;
  if (Math.abs(d.premium) < minPremium) return false;
  return true;
}
