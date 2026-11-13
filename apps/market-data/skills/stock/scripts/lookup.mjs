// /stock's suggestions: the US-listed companies whose ticker or name starts
// with what was typed. The terminal runs this through app_command.lookup as the
// user types and shows each row it prints; picking one runs /stock with it.
import { companies, tickerOf } from "./sources.mjs";

const MAX_ROWS = 10;
const POPULAR = ["nvda", "msft", "aapl", "amzn", "googl", "meta", "tsla", "coin"];

const typed = (process.argv[2] ?? "").trim().toLowerCase().replace(/^\$/, "");
// A name keeps its dots; a ticker reads as /stock reads it.
const ticker = tickerOf(typed);

// Under the 3 s app_command.lookup gives a script.
const list = await companies(2_800);
if (list == null) {
  console.error("The SEC's list of companies could not be read.");
  process.exit(1);
}

const row = (c) => ({
  id: c.ticker,
  lead: { text: c.ticker.toUpperCase(), tone: "accent" },
  main: c.name,
  run: `/stock ${c.ticker}`,
});

// The ticker typed exactly, then tickers and then names that start with it, each
// in the SEC's order, which lists the largest companies first.
const seen = new Set();
const matches = typed === ""
  ? POPULAR.map((t) => list.find((c) => c.ticker === t)).filter(Boolean)
  : [
      ...list.filter((c) => c.ticker === ticker),
      ...list.filter((c) => c.ticker.startsWith(ticker)),
      ...list.filter((c) => c.name.toLowerCase().startsWith(typed)),
    ].filter((c) => !seen.has(c.ticker) && seen.add(c.ticker));
process.stdout.write(`${JSON.stringify({ rows: matches.slice(0, MAX_ROWS).map(row) })}\n`);
