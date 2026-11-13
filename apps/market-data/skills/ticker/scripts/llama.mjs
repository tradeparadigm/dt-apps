// DefiLlama's free API, keyless: TVL, fees, revenue, DEX volume and
// stablecoin supply. Only the index of what it tracks is cached; every figure
// is read live.
import { readList, writeList } from "./cache.mjs";

const API = "https://api.llama.fi";
const STABLES = "https://stablecoins.llama.fi";
const INDEX_MS = 24 * 3_600_000;
// Leaves out the daily series, which are most of each answer.
const SUMMARY = "excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true";

async function get(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(8_000) });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** DefiLlama's own slug for a protocol or stablecoin name. */
export const slugOf = (name) => name.toLowerCase().replace(/ /g, "-").replace(/'/g, "");

const text = (v) => (typeof v === "string" && v !== "" ? v : null);

function buildIndex(config, chains, stables) {
  const entries = [];
  for (const c of Array.isArray(chains) ? chains : []) {
    if (!text(c?.name)) continue;
    entries.push({ kind: "chain", name: c.name, cmc: text(c.cmcId), gecko: text(c.gecko_id), tvl: Number(c.tvl) || 0 });
  }
  // A protocol with no fee adapter is a treasury, a bridge or a chain's own
  // listing, with nothing to show beside the chain.
  const protocols = Array.isArray(config?.protocols) ? config.protocols : [];
  const withFees = new Set();
  for (const p of protocols) {
    if (p?.dimensions?.fees == null) continue;
    if (text(p.parentProtocol)) withFees.add(p.parentProtocol);
    if (text(p.name)) entries.push({ kind: "protocol", name: p.name, cmc: text(p.cmcId), gecko: text(p.gecko_id), parent: false });
  }
  for (const p of Array.isArray(config?.parentProtocols) ? config.parentProtocols : []) {
    if (!withFees.has(p?.id) || !text(p.name)) continue;
    entries.push({ kind: "protocol", name: p.name, cmc: text(p.cmcId), gecko: text(p.gecko_id), parent: true });
  }
  for (const s of Array.isArray(stables?.peggedAssets) ? stables.peggedAssets : []) {
    if (!text(s?.name) || !text(s.id)) continue;
    entries.push({ kind: "stable", name: s.name, id: s.id, gecko: text(s.gecko_id), symbol: text(s.symbol)?.toLowerCase() ?? null });
  }
  return entries;
}

/** Every chain, protocol and stablecoin DefiLlama tracks, or null when it cannot be read. */
export async function llamaIndex() {
  const { fresh, stale } = readList("llama-index.json", INDEX_MS);
  if (fresh) return fresh;
  const [config, chains, stables] = await Promise.all([
    get(`${API}/config`),
    get(`${API}/v2/chains`),
    get(`${STABLES}/stablecoins`),
  ]);
  if (config == null || chains == null || stables == null) return stale ?? null;
  const entries = buildIndex(config, chains, stables);
  writeList("llama-index.json", entries);
  return entries;
}

/**
 * What DefiLlama tracks for a CoinMarketCap coin: `{stable}`, or `{chain,
 * protocol}` with either missing. A coin matches by CoinMarketCap id, else by
 * its slug as a CoinGecko id, which the two sites mostly share.
 */
export function match(index, coin) {
  const id = String(coin.id);
  const slug = coin.slug;
  const is = (e) => e.cmc === id || (slug != null && e.gecko === slug);
  const stablecoin = Array.isArray(coin.tags) && coin.tags.some((t) => (t?.slug ?? t) === "stablecoin");
  if (stablecoin) {
    const symbol = String(coin.symbol).toLowerCase();
    const stable = index.find((e) => e.kind === "stable" && (e.gecko === slug || e.symbol === symbol));
    if (stable) return { stable };
  }
  // Two chains can share a coin (BSC and Binance): the one holding more.
  const chain = index.filter((e) => e.kind === "chain" && is(e)).sort((a, b) => b.tvl - a.tvl)[0];
  const protocol = index.filter((e) => e.kind === "protocol" && is(e)).sort((a, b) => b.parent - a.parent)[0];
  return { chain, protocol };
}

const total = (body, key) => (typeof body?.[key] === "number" ? body[key] : null);

/** The figures for a match, read live: `{chain, protocol, stable}`, each null when absent or unread. */
export async function fundamentals({ chain, protocol, stable }) {
  if (stable) {
    // The list, at about 0.5 MB; one stablecoin's own answer carries its whole history.
    const body = await get(`${STABLES}/stablecoins`);
    const s = Array.isArray(body?.peggedAssets) ? body.peggedAssets.find((a) => a?.id === stable.id) : undefined;
    const now = total(s?.circulating, "peggedUSD");
    const chains = Array.isArray(s?.chains) ? s.chains.length : null;
    return { stable: now == null ? null : { name: stable.name, supply: now, monthAgo: total(s.circulatingPrevMonth, "peggedUSD"), chains } };
  }
  const slug = protocol ? encodeURIComponent(slugOf(protocol.name)) : null;
  const chainName = chain ? encodeURIComponent(chain.name) : null;
  const [chains, dexs, stableChains, chainFees, tvl, fees, revenue] = await Promise.all([
    chain ? get(`${API}/v2/chains`) : null,
    chain ? get(`${API}/overview/dexs/${chainName}?${SUMMARY}`) : null,
    chain ? get(`${STABLES}/stablecoinchains`) : null,
    chain && !protocol ? get(`${API}/overview/fees/${chainName}?${SUMMARY}`) : null,
    protocol && !chain ? get(`${API}/tvl/${slug}`) : null,
    protocol ? get(`${API}/summary/fees/${slug}?dataType=dailyFees&${SUMMARY}`) : null,
    protocol ? get(`${API}/summary/fees/${slug}?dataType=dailyRevenue&${SUMMARY}`) : null,
  ]);
  const named = (list) => (Array.isArray(list) ? list.find((c) => c?.name === chain.name) : undefined);
  return {
    chain: chain && {
      name: chain.name,
      tvl: typeof named(chains)?.tvl === "number" ? named(chains).tvl : null,
      stablecoins: total(named(stableChains)?.totalCirculatingUSD, "peggedUSD"),
      dexVolume: total(dexs, "total24h"),
      fees30d: total(chainFees, "total30d"),
    },
    protocol: protocol && {
      name: protocol.name,
      slug: slugOf(protocol.name),
      tvl: typeof tvl === "number" ? tvl : null,
      fees30d: total(fees, "total30d"),
      revenue1y: total(revenue, "total1y"),
    },
  };
}
