// /ticker: prints USD price and 24 hour change for each symbol, from CoinGecko.
// The app_command tool passes everything the user typed as one argument.
import { SYMBOL, symbolWords } from "./symbols.mjs";

const API = "https://api.coingecko.com/api/v3/simple/price";

const symbols = [...new Set(symbolWords(process.argv[2]))];
if (symbols.length === 0) {
  console.log("Usage: /ticker <symbol> [symbol ...]");
  process.exit(0);
}
if (!symbols.every((symbol) => SYMBOL.test(symbol))) {
  console.error("A symbol can hold only letters and digits. Type the coin's symbol, like btc.");
  process.exit(1);
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
const prices = Object.fromEntries(Object.entries(body).map(([key, quote]) => [key.toLowerCase(), quote]));

// The chat renders the reply as markdown, which joins plain lines into one.
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumSignificantDigits: 7 });
for (const symbol of symbols) {
  const quote = prices[symbol];
  const name = symbol.toUpperCase();
  if (quote?.usd === undefined) {
    console.log(`- ${name}: no price found`);
    continue;
  }
  const change = quote.usd_24h_change;
  const changeText = typeof change === "number" ? ` (${change >= 0 ? "+" : ""}${change.toFixed(2)}% 24h)` : "";
  console.log(`- ${name} ${usd.format(quote.usd)}${changeText}`);
}
