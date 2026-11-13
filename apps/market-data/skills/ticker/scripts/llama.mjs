// DefiLlama's free API, keyless: TVL, fees, revenue, DEX volume and
// stablecoin supply. Only the index of what it tracks is cached; every figure
// is read live.
import { readList, writeList } from "./cache.mjs";

const API = "https://api.llama.fi";
const STABLES = "https://stablecoins.llama.fi";
const INDEX_MS = 24 * 3_600_000;
// Leaves out the daily series, which are most of each answer.
const SUMMARY = "excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true";

async function get(url, signal) {
  try {
    const response = await fetch(url, { signal });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** DefiLlama's own slug for a protocol or stablecoin name. */
export const slugOf = (name) => name.toLowerCase().replace(/ /g, "-").replace(/'/g, "");

const text = (v) => (typeof v === "string" && v !== "" ? v : null);
const lower = (v) => text(v)?.toLowerCase() ?? null;

/** A website's domain, its host's last two labels (`app.lighter.xyz` is `lighter.xyz`), or null. */
export function hostOf(url) {
  try {
    return new URL(url).hostname.split(".").slice(-2).join(".") || null;
  } catch {
    return null;
  }
}

function buildIndex(config, chains, stables) {
  const entries = [];
  for (const c of Array.isArray(chains) ? chains : []) {
    if (!text(c?.name)) continue;
    entries.push({ kind: "chain", name: c.name, cmc: text(c.cmcId), gecko: text(c.gecko_id), symbol: lower(c.tokenSymbol), tvl: Number(c.tvl) || 0 });
  }
  // A protocol with no fee adapter is a treasury, a bridge or a chain's own
  // listing, with nothing to show beside the chain.
  const protocols = Array.isArray(config?.protocols) ? config.protocols : [];
  const withFees = new Set();
  for (const p of protocols) {
    if (p?.dimensions?.fees == null) continue;
    if (text(p.parentProtocol)) withFees.add(p.parentProtocol);
    if (text(p.name)) entries.push({ kind: "protocol", name: p.name, cmc: text(p.cmcId), gecko: text(p.gecko_id), symbol: lower(p.symbol), host: hostOf(p.url), parent: false });
  }
  for (const p of Array.isArray(config?.parentProtocols) ? config.parentProtocols : []) {
    if (!withFees.has(p?.id) || !text(p.name)) continue;
    entries.push({ kind: "protocol", name: p.name, cmc: text(p.cmcId), gecko: text(p.gecko_id), symbol: lower(p.symbol), host: hostOf(p.url), parent: true });
  }
  for (const s of Array.isArray(stables?.peggedAssets) ? stables.peggedAssets : []) {
    if (!text(s?.name) || !text(s.id)) continue;
    entries.push({ kind: "stable", name: s.name, id: s.id, gecko: text(s.gecko_id), symbol: lower(s.symbol) });
  }
  return entries;
}

/**
 * Every chain, protocol and stablecoin DefiLlama tracks, or null when it cannot
 * be read. A failed refresh keeps the old index for another day, so a
 * DefiLlama that is down costs one wait a day.
 */
export async function llamaIndex(signal) {
  const { fresh, stale } = readList("llama-index.json", INDEX_MS);
  if (fresh) return fresh;
  const [config, chains, stables] = await Promise.all([
    get(`${API}/config`, signal),
    get(`${API}/v2/chains`, signal),
    get(`${STABLES}/stablecoins`, signal),
  ]);
  if (config == null || chains == null || stables == null) {
    if (stale) writeList("llama-index.json", stale);
    return stale ?? null;
  }
  const entries = buildIndex(config, chains, stables);
  writeList("llama-index.json", entries);
  return entries;
}

/**
 * What DefiLlama tracks for a CoinMarketCap coin: `{stable}`, `{chain}` or
 * `{protocol}`, else `{}`. A coin matches by CoinMarketCap id, else by its slug
 * as a CoinGecko id with the same symbol (a chain DefiLlama gives none, X Layer,
 * passes), and for a protocol the same website:
 * the two sites mostly share slugs, and a slug and symbol alone have named
 * another project. A coin with its own chain is the chain, since DefiLlama
 * files a chain's foundation under the chain's coin.
 */
export function match(index, coin, website) {
  const id = String(coin.id);
  const slug = coin.slug;
  const symbol = String(coin.symbol).toLowerCase();
  const site = hostOf(website);
  const is = (e) =>
    e.cmc === id ||
    (slug != null && e.gecko === slug && (e.kind === "chain" ? e.symbol == null || e.symbol === symbol : e.symbol === symbol && site != null && e.host === site));
  const stablecoin = Array.isArray(coin.tags) && coin.tags.some((t) => (t?.slug ?? t) === "stablecoin");
  if (stablecoin) {
    // Many stablecoins share a symbol; the slug names one.
    const stables = index.filter((e) => e.kind === "stable");
    const stable = stables.find((e) => e.gecko === slug) ?? stables.find((e) => e.symbol === symbol);
    return stable ? { stable } : {};
  }
  // Two chains can share a coin's id (Flare and Terra Classic): the one with
  // its slug, then the one holding more (BSC over Binance).
  const ownSlug = (e) => (e.gecko === slug ? 1 : 0);
  const chain = index.filter((e) => e.kind === "chain" && is(e)).sort((a, b) => ownSlug(b) - ownSlug(a) || b.tvl - a.tvl)[0];
  if (chain) return { chain };
  const protocol = index.filter((e) => e.kind === "protocol" && is(e)).sort((a, b) => b.parent - a.parent)[0];
  return protocol ? { protocol } : {};
}

const total = (body, key) => (typeof body?.[key] === "number" ? body[key] : null);

/** The figures for a match, read live: `{chain}`, `{protocol}` or `{stable}`, null when unread. */
export async function fundamentals({ chain, protocol, stable }, signal) {
  if (stable) {
    // The list, at about 0.5 MB; one stablecoin's own answer carries its whole history.
    const body = await get(`${STABLES}/stablecoins`, signal);
    const s = Array.isArray(body?.peggedAssets) ? body.peggedAssets.find((a) => a?.id === stable.id) : undefined;
    const now = total(s?.circulating, "peggedUSD");
    const chains = Array.isArray(s?.chains) ? s.chains.length : null;
    return { stable: now == null ? null : { name: stable.name, supply: now, monthAgo: total(s.circulatingPrevMonth, "peggedUSD"), chains } };
  }
  if (chain) {
    const name = encodeURIComponent(chain.name);
    const [chains, dexs, stableChains, fees, revenue] = await Promise.all([
      get(`${API}/v2/chains`, signal),
      get(`${API}/overview/dexs/${name}?${SUMMARY}`, signal),
      get(`${STABLES}/stablecoinchains`, signal),
      get(`${API}/overview/fees/${name}?dataType=dailyFees&${SUMMARY}`, signal),
      get(`${API}/overview/fees/${name}?dataType=dailyRevenue&${SUMMARY}`, signal),
    ]);
    const named = (list) => (Array.isArray(list) ? list.find((c) => c?.name === chain.name) : undefined);
    return {
      chain: {
        name: chain.name,
        tvl: total(named(chains), "tvl"),
        stablecoins: total(named(stableChains)?.totalCirculatingUSD, "peggedUSD"),
        dexVolume: total(dexs, "total24h"),
        fees30d: total(fees, "total30d"),
        revenue1y: total(revenue, "total1y"),
      },
    };
  }
  if (protocol) {
    const slug = encodeURIComponent(slugOf(protocol.name));
    const [tvl, fees, revenue] = await Promise.all([
      get(`${API}/tvl/${slug}`, signal),
      get(`${API}/summary/fees/${slug}?dataType=dailyFees&${SUMMARY}`, signal),
      get(`${API}/summary/fees/${slug}?dataType=dailyRevenue&${SUMMARY}`, signal),
    ]);
    return {
      protocol: {
        name: protocol.name,
        slug: slugOf(protocol.name),
        tvl: typeof tvl === "number" ? tvl : null,
        fees30d: total(fees, "total30d"),
        revenue1y: total(revenue, "total1y"),
      },
    };
  }
  return {};
}
