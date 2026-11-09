// /ticker: prints USD price and 24 hour change for each symbol, from CoinGecko.
// The app_command tool passes everything the user typed as one argument.
const API = "https://api.coingecko.com/api/v3/simple/price";

const symbols = [...new Set((process.argv[2] ?? "").toLowerCase().split(/[\s,]+/).filter(Boolean))];
if (symbols.length === 0) {
  console.log("Usage: /ticker <symbol> [symbol ...]");
  process.exit(0);
}
if (!symbols.every((symbol) => /^[a-z0-9]+$/.test(symbol))) {
  console.error("A symbol can hold only letters and digits.");
  process.exit(1);
}

const url = `${API}?${new URLSearchParams({
  symbols: symbols.join(","),
  vs_currencies: "usd",
  include_24hr_change: "true",
})}`;

const response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
if (!response.ok) {
  console.error(`CoinGecko answered ${response.status}.`);
  process.exit(1);
}
const prices = await response.json();

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumSignificantDigits: 7 });
for (const symbol of symbols) {
  const quote = prices[symbol];
  const name = symbol.toUpperCase();
  if (quote?.usd === undefined) {
    console.log(`${name}: no price found`);
    continue;
  }
  const change = quote.usd_24h_change;
  const changeText = typeof change === "number" ? ` (${change >= 0 ? "+" : ""}${change.toFixed(2)}% 24h)` : "";
  console.log(`${name} ${usd.format(quote.usd)}${changeText}`);
}
