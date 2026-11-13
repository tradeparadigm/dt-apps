// /ticker's three replies, each a plain line and then a spec the terminal
// draws from its catalog (metric_card, labeled_output, data_table,
// performance_chart, markdown, alert_banner). A section whose read fails is
// left out. Each returns null when its first read fails, for the caller's
// fallback.
import { cmcQuotes, get, usd } from "./cmc.mjs";
import { count, day, direction, finite, money, pct, price, pts } from "./format.mjs";

const ACCENT = "#1bc1cf";

const card = (label, value, dir) => ({
  component: "metric_card",
  props: { label, value, ...(dir ? { direction: dir } : {}) },
});
const row = (label, value, dir) => ({
  component: "labeled_output",
  props: { label, value, ...(dir ? { direction: dir } : {}) },
});
const joined = (...parts) => parts.filter((p) => typeof p === "string" && p !== "").join(" · ");

/** The reply: the line every host shows, then the spec, which the terminal finds from the first brace. */
const reply = (line, children) =>
  `${line.replace(/[{}]/g, "")}\n\n${JSON.stringify({ layout: "stack", children })}`;

function chart(label, points, yAxis) {
  const values = points.filter((p) => finite(p.value));
  if (values.length < 2) return null;
  return {
    component: "performance_chart",
    props: { label, yAxis, series: [{ name: label, color: ACCENT, values }] },
  };
}

function fearLabel(value) {
  return value >= 55 ? "up" : value <= 45 ? "down" : undefined;
}

function season(index) {
  if (index >= 75) return "Altcoin season";
  if (index <= 25) return "Bitcoin season";
  return "Mixed";
}

export async function market(env) {
  const [global, fear, fearDays, alt, top100, top20] = await Promise.all([
    get(env, "v1/global-metrics/quotes/latest", { convert: "USD" }),
    get(env, "v3/fear-and-greed/latest"),
    get(env, "v3/fear-and-greed/historical", { limit: "30" }),
    get(env, "v1/altcoin-season-index/latest"),
    get(env, "v3/index/cmc100-latest"),
    get(env, "v3/index/cmc20-latest"),
  ]);
  const g = global?.data;
  const totals = usd(g?.quote);
  if (g == null || totals == null) return null;

  const cap = money(totals.total_market_cap);
  const capChange = totals.total_market_cap_yesterday_percentage_change;
  const volChange = totals.total_volume_24h_yesterday_percentage_change;
  const cards = [];
  if (cap) cards.push(card("Total market cap", joined(cap, pct(capChange, 1)), direction(capChange)));
  const vol = money(totals.total_volume_24h);
  if (vol) cards.push(card("24h volume", joined(vol, pct(volChange, 1)), direction(volChange)));
  const f = fear?.data;
  if (finite(f?.value)) {
    cards.push(card("Fear & Greed", joined(String(f.value), f.value_classification), fearLabel(f.value)));
  }
  for (const [name, now, then] of [
    ["BTC", g.btc_dominance, g.btc_dominance_yesterday],
    ["ETH", g.eth_dominance, g.eth_dominance_yesterday],
  ]) {
    if (!finite(now)) continue;
    const change = finite(then) ? now - then : null;
    cards.push(card(`${name} dominance`, joined(`${now.toFixed(1)}%`, pts(change)), direction(change)));
  }
  const a = alt?.data?.altcoin_index;
  if (finite(a)) cards.push(card("Altcoin season", joined(`${a} / 100`, season(a))));

  const children = [{ layout: "grid", columns: 3, children: cards }];
  for (const [label, body] of [
    ["Top-100 index", top100],
    ["Top-20 index", top20],
  ]) {
    const value = body?.data?.value;
    const change = body?.data?.value_24h_percentage_change;
    if (!finite(value)) continue;
    const text = value.toLocaleString("en-US", { maximumFractionDigits: 1 });
    children.push(row(label, joined(text, pct(change, 1) && `${pct(change, 1)} 24h`), direction(change)));
  }
  const days = Array.isArray(fearDays?.data) ? fearDays.data : [];
  const fearChart = chart(
    "Fear & Greed, 30 days",
    days
      .map((d) => ({ at: /^\d+$/.test(String(d?.timestamp)) ? Number(d.timestamp) * 1000 : Date.parse(d?.timestamp), d }))
      .filter((p) => Number.isFinite(p.at))
      .sort((x, y) => x.at - y.at)
      .map(({ d }) => ({ name: day(d.timestamp), value: d.value })),
    false,
  );
  if (fearChart) children.push(fearChart);

  const line = joined(
    cap && `Market cap ${cap}${pct(capChange, 1) ? ` (${pct(capChange, 1)})` : ""}`,
    vol && `24h volume ${vol}`,
    finite(f?.value) && `Fear & Greed ${f.value} ${f.value_classification ?? ""}`.trim(),
    finite(g.btc_dominance) && `BTC ${g.btc_dominance.toFixed(1)}%`,
  );
  return reply(line, children);
}

const COLUMNS = [
  ["coin", "Coin", "left"],
  ["price", "Price"],
  ["d1", "24h"],
  ["d7", "7d"],
  ["d30", "30d"],
  ["rank", "Rank"],
  ["cap", "Mkt cap"],
  ["volume", "Volume"],
].map(([key, header, align = "right"]) => ({ key, header, align }));

export async function coins(env, symbols) {
  const quotes = await cmcQuotes(symbols, env);
  if (quotes == null) return null;
  const rows = [];
  const lines = [];
  const missing = [];
  for (const symbol of symbols) {
    const found = quotes.get(symbol);
    if (found == null) {
      missing.push(symbol);
      continue;
    }
    const { coin, usd: q } = found;
    rows.push({
      coin: coin.symbol,
      price: price(q.price),
      d1: pct(q.percent_change_24h),
      d7: pct(q.percent_change_7d, 1),
      d30: pct(q.percent_change_30d, 1),
      rank: finite(coin.cmc_rank) ? `#${coin.cmc_rank}` : null,
      cap: money(q.market_cap),
      volume: money(q.volume_24h),
    });
    lines.push(joined(`${coin.symbol} ${price(q.price)}`, pct(q.percent_change_24h)));
  }
  const children = [];
  if (rows.length > 0) children.push({ component: "data_table", props: { columns: COLUMNS, rows } });
  if (missing.length > 0) {
    const others = rows.length === 0 ? "" : rows.length === 1 ? " The other is listed above." : ` The other ${rows.length} are listed above.`;
    const names = missing.map((s) => `\`${s}\``).join(", ");
    children.push({ component: "alert_banner", props: { variant: "warning", message: `No coin found for ${names}.${others}` } });
    lines.push(`No coin found for ${missing.join(", ")}`);
  }
  return reply(lines.join(" · "), children);
}

/** The first sentence, or the first 240 characters. */
function firstSentence(text) {
  const plain = String(text ?? "").replace(/\s+/g, " ").trim();
  const end = plain.search(/[.!?](\s|$)/);
  const sentence = end === -1 ? plain : plain.slice(0, end + 1);
  return sentence.length > 240 ? `${sentence.slice(0, 239)}…` : sentence;
}

const firstURL = (list) => (Array.isArray(list) ? list.find((u) => /^https:\/\//.test(u)) : undefined);

function onChainBanner(security, liquidity) {
  const s = security?.data;
  const pools = Array.isArray(liquidity?.data) ? liquidity.data : [];
  const latest = pools.at(-1)?.liquidityUsd;
  const parts = [];
  if (finite(Number(latest))) parts.push(`${money(Number(latest))} DEX liquidity`);
  const risks = [
    ["honeypotStatus", "honeypot"],
    ["mintableStatus", "mintable"],
    ["rugPullStatus", "rug pull"],
  ].filter(([key]) => s?.[key] === 1 || s?.[key] === true || s?.[key] === "1");
  if (s != null) parts.push(risks.length === 0 ? "no honeypot, mint or rug pull risk found" : `risk: ${risks.map(([, name]) => name).join(", ")}`);
  if (parts.length === 0) return null;
  return {
    component: "alert_banner",
    props: { variant: risks.length === 0 ? "info" : "warning", message: parts.join(" · ") },
  };
}

function aggregateVenues(pairs, total) {
  const venues = [];
  const seen = new Set();
  for (const pair of Array.isArray(pairs) ? pairs : []) {
    const name = pair?.exchange?.name;
    const q = usd(pair?.quote);
    if (typeof name !== "string" || seen.has(name) || !finite(q?.volume_24h)) continue;
    seen.add(name);
    venues.push({
      venue: name,
      pair: pair.market_pair ?? null,
      price: price(q.price),
      volume: money(q.volume_24h),
      share: finite(total) && total > 0 ? `${((q.volume_24h / total) * 100).toFixed(1)}%` : null,
    });
    if (venues.length === 5) break;
  }
  return venues;
}

const VENUE_COLUMNS = [
  ["venue", "Venue", "left"],
  ["pair", "Pair", "left"],
  ["price", "Price"],
  ["volume", "24h volume"],
  ["share", "Share"],
].map(([key, header, align = "right"]) => ({ key, header, align }));

/** A keyed answer's entry for one coin: `data[id]`, or `data` itself. */
const entry = (body, id) => body?.data?.[id] ?? body?.data?.[String(id)] ?? body?.data;

export async function coin(env, symbol) {
  const quotes = await cmcQuotes([symbol], env);
  if (quotes == null) return null;
  const found = quotes.get(symbol);
  if (found == null) return `No coin found for \`${symbol}\`.`;
  const { coin: c, usd: q } = found;
  const id = String(c.id);
  const token = c.platform?.token_address;
  const chain = c.platform?.slug;
  const [perf, history, pairs, info, security, liquidity] = await Promise.all([
    get(env, "v2/cryptocurrency/price-performance-stats/latest", { id, time_period: "all_time,7d,30d,90d,365d", convert: "USD" }),
    get(env, "v3/cryptocurrency/quotes/historical", { id, count: "30", interval: "1d", convert: "USD" }),
    get(env, "v2/cryptocurrency/market-pairs/latest", { id, limit: "20", convert: "USD" }),
    get(env, "v2/cryptocurrency/info", { id }),
    token && chain ? get(env, "v1/dex/security/detail", { platformName: chain, address: token }) : null,
    token && chain ? get(env, "v1/dex/token-liquidity/query", { platform: chain, address: token, interval: "1d", limit: "1" }) : null,
  ]);

  const head = joined(c.name, c.symbol, finite(c.cmc_rank) ? `#${c.cmc_rank}` : null);
  const d1 = q.percent_change_24h;
  const cards = [card(head, joined(price(q.price), pct(d1)), direction(d1))];
  const cap = money(q.market_cap);
  if (cap) {
    const share = finite(q.market_cap_dominance) ? `${q.market_cap_dominance.toFixed(1)}% of market` : null;
    cards.push(card("Market cap", joined(cap, share)));
  }
  const vol = money(q.volume_24h);
  if (vol) cards.push(card("24h volume", joined(vol, pct(q.volume_change_24h, 1)), direction(q.volume_change_24h)));
  const children = [{ layout: "grid", columns: 3, children: cards }];

  const points = (entry(history, id)?.quotes ?? (Array.isArray(history?.data) ? history.data[0]?.quotes : null)) ?? [];
  const priceChart = chart(
    "Price, 30 days",
    (Array.isArray(points) ? points : []).map((p) => ({ name: day(p?.timestamp), value: usd(p?.quote)?.price })),
    true,
  );
  if (priceChart) children.push(priceChart);

  const periods = entry(perf, id)?.periods ?? {};
  const change = (key, fallback) => usd(periods[key]?.quote)?.percent_change ?? fallback;
  const rows = [];
  for (const [label, key, fallback] of [
    ["7d", "7d", q.percent_change_7d],
    ["30d", "30d", q.percent_change_30d],
    ["90d", "90d", q.percent_change_90d],
    ["1y", "365d"],
  ]) {
    const value = change(key, fallback);
    if (finite(value)) rows.push(row(label, pct(value, 1), direction(value)));
  }
  const high = usd(periods.all_time?.quote)?.high;
  if (finite(high) && high > 0) {
    const below = ((high - q.price) / high) * 100;
    rows.push(row("All-time high", joined(price(high), below > 0 ? `${below.toFixed(0)}% below` : "at the high")));
  }
  const unit = (n) => (count(n) ? `${count(n)} ${c.symbol}` : null);
  if (unit(c.circulating_supply)) rows.push(row("Circulating", unit(c.circulating_supply)));
  if (unit(c.max_supply)) rows.push(row("Max supply", unit(c.max_supply)));
  const about = entry(info, id);
  const launched = String(about?.date_launched ?? "").slice(0, 4);
  if (/^\d{4}$/.test(launched)) rows.push(row("Launched", launched));
  if (rows.length > 0) children.push({ layout: "grid", columns: 2, children: rows });

  const venues = aggregateVenues(entry(pairs, id)?.market_pairs, q.volume_24h);
  if (venues.length > 0) children.push({ component: "data_table", props: { columns: VENUE_COLUMNS, rows: venues } });

  const sentence = firstSentence(about?.description);
  const links = [
    ["Website", firstURL(about?.urls?.website)],
    ["X", firstURL(about?.urls?.twitter)],
    ["Explorer", firstURL(about?.urls?.explorer)],
  ]
    .filter(([, url]) => url)
    .map(([name, url]) => `[${name}](${url})`)
    .join(" · ");
  const markdown = [sentence, links].filter(Boolean).join("\n\n");
  if (markdown) children.push({ component: "markdown", props: { content: markdown } });

  const banner = token ? onChainBanner(security, liquidity) : null;
  if (banner) children.push(banner);

  return reply(joined(`${c.name} (${c.symbol}) ${price(q.price)}`, pct(d1) && `${pct(d1)} 24h`, finite(c.cmc_rank) ? `rank #${c.cmc_rank}` : null), children);
}
