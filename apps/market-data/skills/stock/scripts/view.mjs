// /stock's reply: a plain line, then a spec the terminal draws from its
// catalog. A section whose read failed is left out.
import { change, count, day, direction, finite, money, pct, price } from "../../ticker/scripts/format.mjs";
import { card, chart, joined, markdown, multiple, reply, row } from "../../ticker/scripts/spec.mjs";
import { newsMarkdown } from "../../ticker/scripts/news.mjs";
import { NET_INCOME, REVENUE, sharesOutstanding, trailing } from "./sources.mjs";

// What a reader calls each form, for the ones worth listing.
const FORMS = {
  "10-K": "Annual report",
  "10-Q": "Quarterly report",
  "8-K": "Current report",
  "4": "Insider trade",
  "DEF 14A": "Proxy statement",
  "20-F": "Annual report",
  "6-K": "Current report",
  "S-1": "Registration",
};
const MAX_FILINGS = 8;
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const year = (end) => (typeof end === "string" ? end.slice(0, 4) : null);

function filings(filed, cik) {
  const recent = filed?.filings?.recent;
  if (!Array.isArray(recent?.form)) return null;
  const lines = [];
  for (let i = 0; i < recent.form.length && lines.length < MAX_FILINGS; i++) {
    const form = recent.form[i];
    const doc = recent.primaryDocument?.[i];
    const accession = recent.accessionNumber?.[i];
    if (!(form in FORMS) || typeof doc !== "string" || typeof accession !== "string") continue;
    const url = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, "")}/${doc}`;
    lines.push(`- [${form}](${url}) ${FORMS[form]} · ${day(recent.filingDate?.[i]) ?? ""}`);
  }
  return lines.length > 0 ? `**Recent filings**\n\n${lines.join("\n")}` : null;
}

/** True when a day's close moved by a split's ratio (half or less, or 1.7x or more) after `date`. */
function splitSince(history, date) {
  const since = history.filter((c) => c.at >= Date.parse(date));
  return since.some((c, i) => i > 0 && (c.value / since[i - 1].value <= 0.5 || c.value / since[i - 1].value >= 1.7));
}


/** A company's name as people write it: "MICROSOFT CORP" is Microsoft. */
export function plainName(title) {
  let name = String(title).replace(/\/[a-z]{2}\/?$/i, "").trim();
  for (let before = ""; before !== name; ) {
    before = name;
    // "& Co" stays: it tells Merck & Co from Merck KGaA, and KKR & Co from a cricket team.
    if (/&\s*co\.?$/i.test(name)) break;
    name = name.replace(/[,.]?\s+(inc|corp|corporation|co|company|ltd|plc|holdings?|group|n\.?v|s\.?a|ag|se)\.?$/i, "").trim();
  }
  const letters = name.replace(/&\s*co\.?$/i, "");
  return letters === letters.toUpperCase() ? name.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase()) : name;
}

export function stockCard({ company, filed, figures, who, market, history, session, news }) {
  const symbol = company.ticker.toUpperCase();
  const name = who?.name ?? company.name;
  const shares = sharesOutstanding(figures);
  const revenue = trailing(figures, REVENUE);
  const income = trailing(figures, NET_INCOME);
  // A split since the share count's date makes price times count wrong by its ratio until the next filing.
  const split = shares != null && splitSince(history, shares.end);
  const cap = market && shares && !split ? market.price * shares.value : null;
  // The last session close to close, as a quote shows a day's change, then the perp since.
  const sessionMove = session && change(session.close, session.before);
  const since = market && session ? change(market.price, session.close) : null;

  const children = [];
  if (market) {
    const cards = [
      card(`${symbol} · perp`, price(market.price), direction(since ?? sessionMove)),
    ];
    if (pct(sessionMove)) cards.push(card(`${session.day} session`, pct(sessionMove), direction(sessionMove)));
    if (pct(since)) cards.push(card(`Since ${session.day} close`, pct(since), direction(since)));
    if (money(cap)) cards.push(card("Market cap", money(cap)));
    children.push({ layout: "grid", columns: cards.length, children: cards });
    const priceChart = chart("Perp price, 30 days", history.slice(-30).map((c) => ({ name: day(new Date(c.at).toISOString()), value: c.value })), true);
    if (priceChart) children.push(priceChart);
  }

  const rows = [];
  const add = (label, value) => value && rows.push(row(label, value));
  const role = (r) => r && joined(r.name, r.since && `since ${r.since}`);
  add("CEO", role(who?.ceo));
  add("Chair", role(who?.chair));
  add("Founded", who?.founded);
  add("Headquarters", who?.hq);
  add("Employees", who?.employees && joined(count(who.employees.value), who.employees.at));
  add("Industry", filed?.sicDescription);
  add("Exchange", Array.isArray(filed?.exchanges) ? [...new Set(filed.exchanges.filter(Boolean))].join(", ") : null);
  const yearEnd = /^\d{4}$/.test(filed?.fiscalYearEnd ?? "") ? `${MONTHS[Number(filed.fiscalYearEnd.slice(0, 2)) - 1]} ${Number(filed.fiscalYearEnd.slice(2))}` : null;
  add("Fiscal year ends", yearEnd);
  const period = (f) => (f.fiscal ? `FY${year(f.end)}` : "TTM");
  add(revenue && `Revenue, ${period(revenue)}`, revenue && money(revenue.value));
  add(income && `Net income, ${period(income)}`, income && money(income.value));
  add(income && `P/E, ${period(income)}`, income && multiple(cap / income.value));
  add("Shares outstanding", shares && !split && count(shares.value));
  if (market) {
    add("Perp volume, 24h", money(market.volume));
    add("Perp open interest", money(market.openInterest));
    // Hyperliquid quotes funding per hour.
    add("Funding, annualized", finite(market.funding) ? pct(market.funding * 24 * 365 * 100, 1) : null);
  }
  if (rows.length > 0) children.push({ layout: "grid", columns: 2, children: rows });

  if (who?.board?.length > 0) children.push(markdown(`**Board**: ${who.board.join(", ")}`));
  const newsSection = newsMarkdown(news);
  if (newsSection) children.push(markdown(newsSection));
  const listed = filings(filed, company.cik);
  if (listed) children.push(markdown(listed));
  const sources = [
    `[SEC filings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${company.cik})`,
    who?.page && `[Wikidata](${who.page})`,
  ];
  children.push(markdown(`Sources: ${sources.filter(Boolean).join(" · ")}`));
  if (market === false) {
    children.push({ component: "alert_banner", props: { variant: "info", message: `trade.xyz lists no perp on ${symbol}, so there is no live price.` } });
  }
  if (market === null) {
    children.push({ component: "alert_banner", props: { variant: "warning", message: "The live price could not be read from Hyperliquid. Try again in a minute." } });
  }

  const line = joined(
    `${name} (${symbol})`,
    market && `${price(market.price)} perp`,
    pct(sessionMove) && `${pct(sessionMove)} ${session.day}`,
    pct(since) && `${pct(since)} since close`,
    who?.ceo && `CEO ${who.ceo.name}`,
  );
  return reply(line, children);
}
