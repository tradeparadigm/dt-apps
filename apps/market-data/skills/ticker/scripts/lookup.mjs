// /ticker's suggestions: the coins that complete the last symbol typed.
// The terminal runs this through app_command.lookup as the user types and shows
// each row it prints; picking one runs /ticker with that symbol added.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const API = "https://api.coingecko.com/api/v3/search";
const MAX_ROWS = 10;
// Keyless calls share CoinGecko's per-IP limit with every agent behind the same
// NAT, so one answer serves every keystroke that asks it again for five minutes.
const CACHE_MS = 5 * 60_000;
const CACHE_DIR = join(tmpdir(), "market-data-ticker-lookup");
const SYMBOL = /^[a-z0-9]+$/;
const MAJORS = [
  ["btc", "Bitcoin"],
  ["eth", "Ethereum"],
  ["sol", "Solana"],
  ["xrp", "XRP"],
  ["bnb", "BNB"],
  ["doge", "Dogecoin"],
  ["hype", "Hyperliquid"],
  ["ada", "Cardano"],
];

const typed = (process.argv[2] ?? "").toLowerCase().replace(/,/g, " ");
const words = typed.split(/\s+/).filter(Boolean).map((word) => word.replace(/^\$/, ""));
const partial = /\s$/.test(typed) || words.length === 0 ? "" : words.pop();
const kept = words.filter((word) => SYMBOL.test(word));

function row(symbol, name, rank) {
  return {
    id: symbol,
    lead: { text: symbol.toUpperCase(), tone: "accent" },
    main: name,
    ...(rank ? { trail: { text: `#${rank}`, tone: "muted" } } : {}),
    run: `/ticker ${[...kept, symbol].join(" ")}`,
  };
}

function print(rows) {
  const fresh = rows.filter((r) => !kept.includes(r.id)).slice(0, MAX_ROWS);
  process.stdout.write(`${JSON.stringify({ rows: fresh })}\n`);
}

if (!partial) {
  print(MAJORS.map(([symbol, name]) => row(symbol, name)));
  process.exit(0);
}
if (!SYMBOL.test(partial)) {
  print([]);
  process.exit(0);
}

const cacheFile = join(CACHE_DIR, `${partial}.json`);
let coins = null;
try {
  const cached = JSON.parse(readFileSync(cacheFile, "utf8"));
  if (Date.now() - cached.at < CACHE_MS) coins = cached.coins;
} catch {}

if (coins == null) {
  try {
    const response = await fetch(`${API}?${new URLSearchParams({ query: partial })}`, {
      // Longer than the terminal waits: a slow answer still fills the cache for
      // the next keystroke that asks the same thing.
      signal: AbortSignal.timeout(2_500),
    });
    if (!response.ok) {
      console.error(`CoinGecko answered ${response.status}.`);
      process.exit(1);
    }
    coins = ((await response.json())?.coins ?? []).map((c) => ({
      symbol: String(c.symbol ?? "").toLowerCase(),
      name: String(c.name ?? ""),
      rank: Number.isInteger(c.market_cap_rank) ? c.market_cap_rank : null,
    }));
  } catch (error) {
    console.error(`Could not reach CoinGecko: ${error?.cause?.code ?? error?.name ?? error}.`);
    process.exit(1);
  }
  try {
    mkdirSync(CACHE_DIR, { recursive: true });
    writeFileSync(cacheFile, JSON.stringify({ at: Date.now(), coins }));
  } catch {}
}

// A symbol that starts with what was typed first, then a name that does, each
// in CoinGecko's order (by market cap), then the rest. /ticker prices a coin by
// its symbol, so one that cannot be typed as one is left out, and so is a
// second coin with a symbol already listed.
const order = (c) => (c.symbol.startsWith(partial) ? 0 : c.name.toLowerCase().startsWith(partial) ? 1 : 2);
const seen = new Set();
const rows = coins
  .map((c, i) => ({ ...c, i }))
  .filter((c) => SYMBOL.test(c.symbol) && !seen.has(c.symbol) && seen.add(c.symbol))
  .sort((a, b) => order(a) - order(b) || a.i - b.i)
  .map((c) => row(c.symbol, c.name, c.rank));
print(rows);
