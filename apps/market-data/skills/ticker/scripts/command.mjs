// /ticker: the market with no symbols, a table for several coins, a full card
// for one, all read live through DIME Terminal's relay. Where the relay cannot
// answer, prices alone from CoinGecko's public API. The app_command tool
// passes everything the user typed as one argument.
import { MARKET_PATH, QUOTES_PATH, relayAbsent } from "./cmc.mjs";
import { price as formatPrice, pct } from "./format.mjs";
import { SYMBOL, symbolWords } from "./symbols.mjs";
import { coin, coins, market } from "./views.mjs";

const API = "https://api.coingecko.com/api/v3/simple/price";

const symbols = [...new Set(symbolWords(process.argv[2]))];
if (!symbols.every((symbol) => SYMBOL.test(symbol))) {
  console.error("A symbol can hold only letters and digits. Type the coin's symbol, like btc.");
  process.exit(1);
}

const view = symbols.length === 0 ? await market(process.env) : symbols.length === 1 ? await coin(process.env, symbols[0]) : await coins(process.env, symbols);
if (view != null) {
  console.log(view);
  process.exit(0);
}
const relayed = !relayAbsent(process.env, symbols.length === 0 ? MARKET_PATH : QUOTES_PATH);
if (symbols.length === 0) {
  console.error(relayed ? "The market overview could not be read. Try again in a minute." : "The market overview isn't available on this agent. Type /ticker btc for a price.");
  process.exit(1);
}

// The chat renders the reply as markdown, which joins plain lines into one.
function print(quotes) {
  for (const symbol of symbols) {
    const quote = quotes.get(symbol);
    const name = symbol.toUpperCase();
    if (quote?.usd === undefined) {
      console.log(`- ${name}: no price found`);
      continue;
    }
    const change = pct(quote.change);
    console.log(`- ${name} ${formatPrice(quote.usd)}${change ? ` (${change.replace("−", "-")} 24h)` : ""}`);
  }
  if (!relayed) console.log("\nMore detail isn't available on this agent.");
}

const url = `${API}?${new URLSearchParams({
  symbols: symbols.join(","),
  vs_currencies: "usd",
  include_24hr_change: "true",
})}`;

let response;
let body;
try {
  response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  body = response.ok ? await response.json() : null;
} catch (error) {
  console.error(`Could not reach CoinGecko: ${error?.cause?.code ?? error?.name ?? error}.`);
  process.exit(1);
}
if (response.status === 401 || response.status === 403) {
  console.error("CoinGecko refused the Demo key. Replace or remove it in Apps, Market Data.");
  process.exit(1);
}
if (!response.ok) {
  console.error(`CoinGecko answered ${response.status}.`);
  process.exit(1);
}
// CoinGecko keys each quote by the symbol as it stores it (POL, stETH).
print(
  new Map(
    Object.entries(body).map(([key, quote]) => [key.toLowerCase(), { usd: quote.usd, change: quote.usd_24h_change }]),
  ),
);
