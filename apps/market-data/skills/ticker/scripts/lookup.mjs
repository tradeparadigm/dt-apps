// /ticker's suggestions: the coins that complete the last symbol typed.
// The terminal runs this through app_command.lookup as the user types and shows
// each row it prints; picking one runs /ticker with that symbol added.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cmcDirectory } from "./cmc.mjs";
import { SYMBOL, partialWord, symbolWords } from "./symbols.mjs";

// The top 250 coins by market cap, read once and matched locally, so typing
// makes no call: keyless calls share CoinGecko's per-IP limit with every agent
// behind the same NAT, and /ticker's own price call spends it too.
const API = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1";
const MAX_ROWS = 10;
const CACHE_MS = 5 * 60_000;
const CACHE_DIR = join(tmpdir(), "market-data-ticker-lookup");
const CACHE_FILE = join(CACHE_DIR, "markets.json");
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

const typed = process.argv[2] ?? "";
const partial = partialWord(typed);
const words = symbolWords(typed);
if (partial) words.pop();
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

// A symbol that starts with what was typed, then a name that does, each by
// rank. /ticker prices a coin by its symbol, so one that cannot be typed as
// one is left out, and so is a second coin with a symbol already listed.
function printMatches(list) {
  const order = (c) => (c.symbol.startsWith(partial) ? 0 : c.name.toLowerCase().startsWith(partial) ? 1 : 2);
  const seen = new Set();
  print(
    list
      .map((c, i) => ({ ...c, i }))
      .filter((c) => order(c) < 2 && SYMBOL.test(c.symbol) && !seen.has(c.symbol) && seen.add(c.symbol))
      .sort((a, b) => order(a) - order(b) || a.i - b.i)
      .map((c) => row(c.symbol, c.name, c.rank)),
  );
}

// Every coin CoinMarketCap tracks, through DIME Terminal's relay; CoinGecko's
// top 250 on an agent without it.
const directory = await cmcDirectory(process.env);
if (directory != null) {
  printMatches(directory);
  process.exit(0);
}

let coins = null;
let stale = null;
let fetched = false;
try {
  const cached = JSON.parse(readFileSync(CACHE_FILE, "utf8"));
  if (Array.isArray(cached.coins)) {
    if (Date.now() - cached.at < CACHE_MS) coins = cached.coins;
    else stale = cached.coins;
  }
} catch {}

// A refused or failed refresh answers from the list on disk, however old.
function fail(message) {
  if (stale == null) {
    console.error(message);
    process.exit(1);
  }
  return stale;
}

if (coins == null) {
  try {
    const response = await fetch(API, {
      // Longer than the terminal waits: a slow answer still fills the cache for
      // the next keystroke.
      signal: AbortSignal.timeout(2_500),
    });
    if (response.ok) {
      const body = await response.json();
      coins = (Array.isArray(body) ? body : []).map((c) => ({
        symbol: String(c.symbol ?? "").toLowerCase(),
        name: String(c.name ?? ""),
        rank: Number.isInteger(c.market_cap_rank) ? c.market_cap_rank : null,
      }));
      fetched = true;
    } else {
      coins = fail(`CoinGecko answered ${response.status}.`);
    }
  } catch (error) {
    coins = fail(`Could not reach CoinGecko: ${error?.cause?.code ?? error?.name ?? error}.`);
  }
}
if (fetched) {
  try {
    mkdirSync(CACHE_DIR, { recursive: true });
    const temp = `${CACHE_FILE}.${process.pid}`;
    writeFileSync(temp, JSON.stringify({ at: Date.now(), coins }));
    renameSync(temp, CACHE_FILE);
  } catch {}
}

printMatches(coins);
