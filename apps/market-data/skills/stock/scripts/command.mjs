// /stock: one US-listed company in full, from the SEC, Wikidata and
// trade.xyz's perp on Hyperliquid, all read live. The app_command tool passes
// everything the user typed as one argument.
import { closes, companies, facts, people, perp, sessions, submissions, tickerOf } from "./sources.mjs";
import { stockCard } from "./view.mjs";

const TICKER = /^[a-z][a-z0-9.-]{0,9}$/;

const typed = (process.argv[2] ?? "").trim().toLowerCase().replace(/^\$/, "");
if (typed === "") {
  console.log("Type a US stock's ticker, like `/stock msft`.");
  process.exit(0);
}
if (!TICKER.test(typed)) {
  console.error("Type one ticker, like msft or brk.b.");
  process.exit(1);
}

const list = await companies();
if (list == null) {
  console.error("The SEC's list of companies could not be read. Try again in a minute.");
  process.exit(1);
}
const ticker = tickerOf(typed);
const company = list.find((c) => c.ticker === ticker);
if (company == null) {
  console.log(`No US-listed company found for \`${typed}\`.`);
  process.exit(0);
}

const [filed, figures, who, market, history, session] = await Promise.all([
  submissions(company.cik),
  facts(company.cik),
  people(company.cik),
  perp(ticker),
  closes(ticker),
  sessions(ticker),
]);
console.log(stockCard({ company, filed, figures, who, market, history, session }));
