// Recent headlines from Google News' RSS search, keyless. A headline, its
// outlet, its age and a link; never an article's text.
const SEARCH = "https://news.google.com/rss/search";
const TIMEOUT_MS = 4_000;
const MAX = 5;

// Listings and forecasts that name a coin without being news about it.
const NOISE = /price prediction|price forecast|live price|price in [a-z]+$|price, news|news, chart|stock quotes?,|company news and|\bconvert \d|tokenized stock|^about /i;

const unescape = (text) =>
  text
    .replace(/<!\[CDATA\[|\]\]>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&amp;/g, "&")
    .trim();

function items(xml) {
  return [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].map(([, body]) => {
    const tag = (name) => {
      const found = body.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`));
      return found ? unescape(found[1]) : "";
    };
    const source = tag("source");
    // Google ends each title with " - <outlet>".
    const title = source && tag("title").endsWith(` - ${source}`) ? tag("title").slice(0, -(source.length + 3)) : tag("title");
    return { title, source, link: tag("link"), at: Date.parse(tag("pubDate")) };
  });
}

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Two outlets running one wire story differ in the last words, if at all.
const story = (title) => title.toLowerCase().replace(/[^a-z0-9 ]/g, "").split(/\s+/).slice(0, 6).join(" ");

/**
 * Up to five headlines from the last two days that name `name` or `symbol`,
 * in Google's order, one per story: `[{title, source, link, at}]`, or null
 * when Google News could not be read.
 */
export async function headlines(name, symbol) {
  const query = `"${name}" ${symbol.toUpperCase()} when:2d`;
  const url = `${SEARCH}?${new URLSearchParams({ q: query, hl: "en-US", gl: "US", ceid: "US:en" })}`;
  let xml;
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!response.ok) return null;
    xml = await response.text();
  } catch {
    return null;
  }
  // The name in any case; the symbol only as a capitalised word, since "SUI" is news and "sui" is Latin.
  // A company's first word too, for "Meta" in news of Meta Platforms; the quoted search already names it in full.
  const first = name.split(/\s+/)[0];
  const byName = new RegExp(first.length >= 4 ? `${escape(name)}|\\b${escape(first)}\\b` : escape(name), "i");
  const bySymbol = new RegExp(`\\b${escape(symbol.toUpperCase())}\\b`);
  const seen = new Set();
  return items(xml)
    .filter((i) => i.title && /^https:\/\//.test(i.link) && Number.isFinite(i.at))
    .filter((i) => byName.test(i.title) || bySymbol.test(i.title))
    .filter((i) => !NOISE.test(i.title))
    .filter((i) => !seen.has(story(i.title)) && seen.add(story(i.title)))
    .slice(0, MAX);
}

/** `3h`, `2d`: how long ago. */
function age(at, now) {
  const hours = Math.max(0, Math.floor((now - at) / 3_600_000));
  return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`;
}

/** The section: a heading linking the search, then one line per headline. */
export function newsMarkdown(list, name, symbol, now = Date.now()) {
  if (!Array.isArray(list) || list.length === 0) return null;
  const search = `https://news.google.com/search?${new URLSearchParams({ q: `"${name}" ${symbol.toUpperCase()}` })}`;
  const line = (i) => `- [${i.title.replace(/[[\]]/g, "")}](${i.link}) · ${[i.source, age(i.at, now)].filter(Boolean).join(" · ")}`;
  return `**News** · from [Google News](${search})\n\n${list.map(line).join("\n")}`;
}
