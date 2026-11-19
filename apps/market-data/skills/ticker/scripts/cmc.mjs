// CoinMarketCap through DIME Terminal's sidecar relay (/api/cmc, beside the
// JEV relay that TERMINAL_JEV_URL names). LiteLLM adds the platform's key, so
// the agent never holds it. null means the relay could not answer (an older
// agent, no key, an error). Nothing here is cached but the coin directory.
import { readList, writeList } from "./cache.mjs";

const JEV_ROUTE = /\/api\/jev$/;
// The coin directory holds names, symbols and ranks, never a price.
const DIRECTORY_MS = 24 * 3_600_000;
// The read each view stands on. A 404 there is a sidecar from before the
// relay, or a LiteLLM with no CoinMarketCap route: nothing to retry.
export const MARKET_PATH = "v1/global-metrics/quotes/latest";
export const QUOTES_PATH = "v3/cryptocurrency/quotes/latest";
const notFound = new Set();

export function relayURL(env, path) {
  const jev = env.TERMINAL_JEV_URL;
  if (typeof jev !== "string" || !JEV_ROUTE.test(jev)) return null;
  return jev.replace(JEV_ROUTE, `/api/cmc/${path}`);
}

/** The answer's JSON, or null for anything but a 200 with JSON. */
export async function get(env, path, params = {}, timeoutMs = 10_000) {
  const base = relayURL(env, path);
  if (base == null) return null;
  const query = new URLSearchParams(params).toString();
  try {
    const response = await fetch(query ? `${base}?${query}` : base, { signal: AbortSignal.timeout(timeoutMs) });
    if (response.status === 404) notFound.add(path);
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** True when this agent has no relay, or the relay had no route for `path`. */
export function relayAbsent(env, path) {
  return relayURL(env, "") == null || notFound.has(path);
}

/** A quote's USD figures: v3 latest sends a list of conversions, historical a map. */
export function usd(quote) {
  if (Array.isArray(quote)) return quote.find((q) => q?.symbol === "USD") ?? null;
  return quote?.USD ?? null;
}

/**
 * Each symbol's highest-ranked coin with a USD price, keyed by the symbol in
 * lower case, or null when the relay could not answer.
 */
export async function cmcQuotes(symbols, env) {
  const body = await get(env, QUOTES_PATH, {
    symbol: symbols.map((s) => s.toUpperCase()).join(","),
    convert: "USD",
  });
  if (!Array.isArray(body?.data)) return null;
  const quotes = new Map();
  for (const coin of body.data) {
    const quote = usd(coin?.quote);
    if (typeof coin?.symbol !== "string" || typeof quote?.price !== "number") continue;
    const symbol = coin.symbol.toLowerCase();
    const held = quotes.get(symbol);
    if (held == null || (coin.cmc_rank ?? Infinity) < (held.coin.cmc_rank ?? Infinity)) {
      quotes.set(symbol, { coin, usd: quote });
    }
  }
  return quotes;
}

/** Every active coin, best rank first: `[{symbol, name, rank}]`. */
export async function cmcDirectory(env) {
  if (relayURL(env, "") == null) return null;
  const { fresh, stale } = readList("cmc-map.json", DIRECTORY_MS);
  if (fresh) return fresh;
  // Under the 3 s app_command.lookup gives a script.
  const body = await get(env, "v1/cryptocurrency/map", {}, 2_800);
  if (!Array.isArray(body?.data)) return stale ?? null;
  const coins = body.data
    .filter((c) => typeof c?.symbol === "string")
    .map((c) => ({ symbol: c.symbol.toLowerCase(), name: String(c.name ?? ""), rank: Number.isInteger(c.rank) ? c.rank : null }))
    .sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity));
  writeList("cmc-map.json", coins);
  return coins;
}
