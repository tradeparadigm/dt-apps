// /stock's sources, all keyless: SEC EDGAR for the company, its financials and
// filings, Wikidata for its people, and Hyperliquid's trade.xyz perp for a
// price. Only the SEC's list of tickers is cached; every figure is read live.
import { readList, writeList } from "../../ticker/scripts/cache.mjs";

// The SEC refuses a request with no User-Agent and asks automated clients to name themselves.
const AGENT = "DIME-Terminal-market-data";
const TICKERS_MS = 24 * 3_600_000;

async function get(url, init = {}, timeoutMs = 8_000) {
  try {
    const response = await fetch(url, {
      ...init,
      headers: { "User-Agent": AGENT, ...init.headers },
      signal: AbortSignal.timeout(timeoutMs),
    });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** Every company the SEC lists with a ticker: `[{ticker, name, cik}]`, lowercase tickers. */
export async function companies(timeoutMs) {
  const { fresh, stale } = readList("sec-tickers.json", TICKERS_MS);
  if (fresh) return fresh;
  const body = await get("https://www.sec.gov/files/company_tickers.json", {}, timeoutMs);
  if (body == null || typeof body !== "object") return stale ?? null;
  const list = Object.values(body)
    .filter((c) => typeof c?.ticker === "string" && Number.isInteger(c.cik_str))
    .map((c) => ({ ticker: c.ticker.toLowerCase(), name: String(c.title ?? ""), cik: c.cik_str }));
  writeList("sec-tickers.json", list);
  return list;
}

const padded = (cik) => String(cik).padStart(10, "0");

/** The SEC's record of the company: names, industry, exchanges and its recent filings. */
export const submissions = (cik) => get(`https://data.sec.gov/submissions/CIK${padded(cik)}.json`);

/** Every figure the company has filed in XBRL. */
export const facts = (cik) => get(`https://data.sec.gov/api/xbrl/companyfacts/CIK${padded(cik)}.json`);

const DAY_MS = 86_400_000;
const days = (f) => (Date.parse(f.end) - Date.parse(f.start)) / DAY_MS;
const yearLong = (f) => days(f) > 350 && days(f) < 380;

/**
 * The latest full fiscal year of a figure from an annual report, `{value, end}`,
 * over every tag that has named it: companies move between tags over the years.
 */
export function annual(body, tags) {
  let best = null;
  for (const tag of tags) {
    const usd = body?.facts?.["us-gaap"]?.[tag]?.units?.USD;
    for (const f of Array.isArray(usd) ? usd : []) {
      if (!/^(10-K|20-F|40-F)/.test(f?.form ?? "") || typeof f.val !== "number" || !yearLong(f)) continue;
      if (best == null || f.end > best.end) best = { value: f.val, end: f.end };
    }
  }
  return best;
}

const near = (a, b, within) => Math.abs(Date.parse(a) - Date.parse(b)) <= within * DAY_MS;

/**
 * The last twelve months of a figure, `{value, end, months: 12}`: the latest
 * annual report's year, plus the year to date since, less the same stretch a
 * year before. With no quarter filed since, the year alone (`fiscal: true`).
 */
export function trailing(body, tags) {
  let best = null;
  for (const tag of tags) {
    const usd = body?.facts?.["us-gaap"]?.[tag]?.units?.USD;
    const facts = (Array.isArray(usd) ? usd : []).filter(
      (f) => /^(10-K|10-Q|20-F|40-F)/.test(f?.form ?? "") && typeof f.val === "number" && f.start && f.end,
    );
    const year = annual(body, [tag]);
    if (year == null) continue;
    // The longest stretch from the start of the new fiscal year, as each quarterly report restates it.
    const sinceYear = facts
      .filter((f) => near(f.start, new Date(Date.parse(year.end) + DAY_MS).toISOString(), 3))
      .sort((a, b) => b.end.localeCompare(a.end))[0];
    const yearBefore = sinceYear && facts.find(
      (f) => near(f.end, new Date(Date.parse(sinceYear.end) - 365 * DAY_MS).toISOString(), 10) && Math.abs(days(f) - days(sinceYear)) <= 10,
    );
    const figure = yearBefore
      ? { value: year.value + sinceYear.val - yearBefore.val, end: sinceYear.end, fiscal: false }
      : { ...year, fiscal: true };
    if (best == null || figure.end > best.end) best = figure;
  }
  return best;
}

export const REVENUE = [
  "RevenueFromContractWithCustomerExcludingAssessedTax",
  "Revenues",
  "SalesRevenueNet",
  "RevenueFromContractWithCustomerIncludingAssessedTax",
];
export const NET_INCOME = ["NetIncomeLoss", "ProfitLoss"];

/**
 * Shares outstanding from the latest filing that reports them, within the
 * last year: the cover page count (one fact per share class, summed), else
 * the balance sheet's.
 */
export function sharesOutstanding(body) {
  for (const list of [
    body?.facts?.dei?.EntityCommonStockSharesOutstanding?.units?.shares,
    body?.facts?.["us-gaap"]?.CommonStockSharesOutstanding?.units?.shares,
  ]) {
    const facts = (Array.isArray(list) ? list : []).filter((f) => typeof f?.val === "number" && typeof f.end === "string");
    if (facts.length === 0) continue;
    const latest = facts.reduce((a, f) => (f.end > a.end || (f.end === a.end && f.filed > a.filed) ? f : a));
    // A count from before the last annual report is a company that stopped reporting it.
    if (Date.now() - Date.parse(latest.end) > 400 * DAY_MS) continue;
    // Later filings restate the same date; only that one filing's classes count.
    const classes = facts.filter((f) => f.end === latest.end && f.accn === latest.accn);
    const total = classes.reduce((sum, f) => sum + f.val, 0);
    if (total > 0) return { value: total, end: latest.end };
  }
  return null;
}

// A role held now: no end date. Each name carries its start, so the newest wins.
const current = (property, variable) => `OPTIONAL {
    ?item p:${property} ?${variable}Statement . ?${variable}Statement ps:${property} ?${variable}Item .
    FILTER NOT EXISTS { ?${variable}Statement pq:P582 ?${variable}End }
    OPTIONAL { ?${variable}Statement pq:P580 ?${variable}Start }
    ?${variable}Item rdfs:label ?${variable}Name FILTER(LANG(?${variable}Name) = "en")
  }`;
const dated = (variable) =>
  `(GROUP_CONCAT(DISTINCT CONCAT(?${variable}Name, "@", COALESCE(STR(?${variable}Start), "")); separator="|") AS ?${variable})`;

const SPARQL = (cik) => `SELECT ?item (SAMPLE(?label) AS ?name) ${dated("ceo")} ${dated("chair")} ${dated("board")}
  (SAMPLE(?inception) AS ?founded) (SAMPLE(?hqName) AS ?hq)
  (GROUP_CONCAT(DISTINCT CONCAT(STR(?staff), "@", COALESCE(STR(?staffAt), "")); separator="|") AS ?staffing)
WHERE {
  VALUES ?cik { "${padded(cik)}" "${cik}" }
  ?item wdt:P5531 ?cik .
  OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label) = "en") }
  ${current("P169", "ceo")}
  ${current("P488", "chair")}
  ${current("P3320", "board")}
  OPTIONAL { ?item wdt:P571 ?inception }
  OPTIONAL { ?item wdt:P159 ?hqItem . ?hqItem rdfs:label ?hqName FILTER(LANG(?hqName) = "en") }
  OPTIONAL { ?item p:P1128 ?staffStatement . ?staffStatement ps:P1128 ?staff . OPTIONAL { ?staffStatement pq:P585 ?staffAt } }
} GROUP BY ?item LIMIT 1`;

/** `value@date` pairs, newest date first; an undated one sorts last. */
function newestFirst(text) {
  return (text ?? "")
    .split("|")
    .filter(Boolean)
    .map((s) => {
      const at = s.lastIndexOf("@");
      return { value: s.slice(0, at), at: s.slice(at + 1) };
    })
    .sort((a, b) => b.at.localeCompare(a.at));
}

/** Whoever started in the role last: `{name, since}`, the year or null. */
function holder(text) {
  const newest = newestFirst(text)[0];
  return newest ? { name: newest.value, since: newest.at.slice(0, 4) || null } : null;
}

/** The company's people and facts from Wikidata, found by its CIK, or null. */
export async function people(cik) {
  const url = `https://query.wikidata.org/sparql?query=${encodeURIComponent(SPARQL(cik))}`;
  const body = await get(url, { headers: { Accept: "application/sparql-results+json" } });
  const row = body?.results?.bindings?.[0];
  if (row?.item == null) return null;
  const value = (k) => (typeof row[k]?.value === "string" && row[k].value !== "" ? row[k].value : null);
  const staff = newestFirst(value("staffing"))
    .map((c) => ({ n: Number(c.value), at: c.at }))
    .filter((c) => Number.isFinite(c.n) && c.n > 0);
  return {
    page: value("item"),
    name: value("name"),
    ceo: holder(value("ceo")),
    chair: holder(value("chair")),
    board: [...new Set(newestFirst(value("board")).map((b) => b.value))].sort(),
    founded: value("founded")?.slice(0, 4) ?? null,
    hq: value("hq"),
    employees: staff[0] ? { value: staff[0].n, at: staff[0].at.slice(0, 4) || null } : null,
  };
}

const HYPERLIQUID = "https://api.hyperliquid.xyz/info";
// trade.xyz's markets that are no company: commodities, currencies and indexes.
// Several share a ticker with a listed company (CL is Colgate, GOLD is Gold.com),
// whose card would otherwise show oil's or gold's price.
const NOT_A_STOCK = new Set([
  "GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "CL", "BRENTOIL", "NATGAS", "HO",
  "EUR", "GBP", "JPY", "SP500", "XYZ100", "JP225", "KR200", "NIFTY",
]);
const info = (body) =>
  get(HYPERLIQUID, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/**
 * trade.xyz's perp on the stock, `{price, volume, openInterest, funding}`;
 * false when it lists none for the company (a delisted market's price is
 * frozen), null when Hyperliquid could not be read.
 */
export async function perp(ticker) {
  const body = await info({ type: "metaAndAssetCtxs", dex: "xyz" });
  const universe = body?.[0]?.universe;
  const contexts = body?.[1];
  if (!Array.isArray(universe) || !Array.isArray(contexts)) return null;
  const name = `xyz:${ticker.toUpperCase()}`;
  const i = universe.findIndex((m) => m?.name === name);
  if (i === -1 || universe[i]?.isDelisted === true || NOT_A_STOCK.has(ticker.toUpperCase())) return false;
  const ctx = contexts[i];
  const price = Number(ctx?.markPx);
  if (!Number.isFinite(price)) return null;
  return {
    price,
    volume: Number(ctx.dayNtlVlm),
    openInterest: Number(ctx.openInterest) * price,
    funding: Number(ctx.funding),
  };
}

/**
 * The perp's daily closes for the last 200 days, oldest first, or none when it
 * lists no perp: the chart's 30, and back past the share count's date to see a split.
 */
export async function closes(ticker) {
  const end = Date.now();
  const body = await info({ type: "candleSnapshot", req: { coin: `xyz:${ticker.toUpperCase()}`, interval: "1d", startTime: end - 200 * DAY_MS, endTime: end } });
  if (!Array.isArray(body)) return [];
  return body.map((c) => ({ at: c?.t, value: Number(c?.c) })).filter((c) => Number.isFinite(c.at) && Number.isFinite(c.value));
}

const NEW_YORK = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/**
 * The perp at the last two US closes, `{day, close, before}`, from its hourly
 * candles: the one ending 4pm New York time on a weekday. The perp trades on
 * through a market holiday, so a holiday counts as a session.
 */
export async function sessions(ticker) {
  const end = Date.now();
  const body = await info({ type: "candleSnapshot", req: { coin: `xyz:${ticker.toUpperCase()}`, interval: "1h", startTime: end - 6 * DAY_MS, endTime: end } });
  if (!Array.isArray(body)) return null;
  const closes = [];
  for (const c of body) {
    const at = Number(c?.t) + 3_600_000;
    const value = Number(c?.c);
    if (!Number.isFinite(at) || !Number.isFinite(value) || at > end) continue;
    const p = Object.fromEntries(NEW_YORK.formatToParts(at).map((x) => [x.type, x.value]));
    if (p.hour === "16" && p.minute === "00" && p.weekday !== "Sat" && p.weekday !== "Sun") closes.push({ day: p.weekday, value });
  }
  const [before, last] = closes.slice(-2);
  return last && before ? { day: last.day, close: last.value, before: before.value } : null;
}
