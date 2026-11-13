// CoinMarketCap through DIME Terminal's sidecar relay (/api/cmc, beside the
// JEV relay that TERMINAL_JEV_URL names). The platform's key is added by
// LiteLLM, so the agent never holds it. null means the relay could not answer
// (an older agent, no key, an error): the caller falls back to CoinGecko.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const JEV_ROUTE = /\/api\/jev$/;
// The coin directory holds names, symbols and ranks, never a price.
const DIRECTORY_MS = 24 * 3_600_000;
const DIRECTORY_FILE = join(tmpdir(), "market-data-ticker-lookup", "cmc-map.json");

export function relayURL(env, path) {
  const jev = env.TERMINAL_JEV_URL;
  if (typeof jev !== "string" || !JEV_ROUTE.test(jev)) return null;
  return jev.replace(JEV_ROUTE, `/api/cmc/${path}`);
}

async function get(env, path, timeoutMs) {
  const url = relayURL(env, path);
  if (url == null) return null;
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** Each symbol's USD price and 24 h change, from its highest-ranked coin. */
export async function cmcQuotes(symbols, env) {
  const query = new URLSearchParams({ symbol: symbols.map((s) => s.toUpperCase()).join(","), convert: "USD" });
  const body = await get(env, `v2/cryptocurrency/quotes/latest?${query}`, 10_000);
  if (body?.data == null || typeof body.data !== "object") return null;
  const quotes = new Map();
  for (const [symbol, coins] of Object.entries(body.data)) {
    const best = (Array.isArray(coins) ? coins : [coins])
      .filter((c) => typeof c?.quote?.USD?.price === "number")
      .sort((a, b) => (a.cmc_rank ?? Infinity) - (b.cmc_rank ?? Infinity))[0];
    if (best) quotes.set(symbol.toLowerCase(), { usd: best.quote.USD.price, change: best.quote.USD.percent_change_24h });
  }
  return quotes;
}

/** Every active coin, best rank first: `[{symbol, name, rank}]`. */
export async function cmcDirectory(env) {
  if (relayURL(env, "") == null) return null;
  try {
    const cached = JSON.parse(readFileSync(DIRECTORY_FILE, "utf8"));
    if (Date.now() - cached.at < DIRECTORY_MS && Array.isArray(cached.coins)) return cached.coins;
  } catch {}
  // Under the 3 s app_command.lookup gives a script.
  const body = await get(env, "v1/cryptocurrency/map", 2_800);
  if (!Array.isArray(body?.data)) return null;
  const coins = body.data
    .filter((c) => typeof c?.symbol === "string")
    .map((c) => ({ symbol: c.symbol.toLowerCase(), name: String(c.name ?? ""), rank: Number.isInteger(c.rank) ? c.rank : null }))
    .sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity));
  try {
    mkdirSync(join(DIRECTORY_FILE, ".."), { recursive: true });
    const temp = `${DIRECTORY_FILE}.${process.pid}`;
    writeFileSync(temp, JSON.stringify({ at: Date.now(), coins }));
    renameSync(temp, DIRECTORY_FILE);
  } catch {}
  return coins;
}
