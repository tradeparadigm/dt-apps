// /stock's reply: a plain line, then a spec the terminal draws from its
// catalog. A section whose read failed is left out.
import { count, day, direction, finite, money, pct, price } from "../../ticker/scripts/format.mjs";
import { NET_INCOME, REVENUE, annual, sharesOutstanding } from "./sources.mjs";

const ACCENT = "#1bc1cf";
const card = (label, value, dir) => ({ component: "metric_card", props: { label, value, ...(dir ? { direction: dir } : {}) } });
const row = (label, value) => ({ component: "labeled_output", props: { label, value } });
const markdown = (content) => ({ component: "markdown", props: { content } });
const joined = (...parts) => parts.filter((p) => typeof p === "string" && p !== "").join(" · ");

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
const multiple = (n) => (finite(n) && n > 0 ? `${n >= 10 ? n.toFixed(0) : n.toFixed(1)}x` : null);

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

export function stockCard({ company, filed, figures, who, market, history }) {
  const symbol = company.ticker.toUpperCase();
  const name = who?.name ?? company.name;
  const shares = sharesOutstanding(figures);
  const revenue = annual(figures, REVENUE);
  const income = annual(figures, NET_INCOME);
  const cap = market && shares ? market.price * shares.value : null;

  const children = [];
  if (market) {
    const cards = [
      card(`${symbol} · perp`, price(market.price), direction(market.change)),
      card("24h", pct(market.change) ?? "–", direction(market.change)),
    ];
    if (money(cap)) cards.push(card("Market cap", money(cap)));
    if (multiple(cap / income?.value)) cards.push(card("P/E", multiple(cap / income.value)));
    children.push({ layout: "grid", columns: cards.length, children: cards });
    const points = history.slice(-30).map((c) => ({ name: day(new Date(c.at).toISOString()), value: c.value }));
    if (points.length >= 2) {
      children.push({
        component: "performance_chart",
        props: { label: "Perp price, 30 days", yAxis: true, series: [{ name: "Perp price, 30 days", color: ACCENT, values: points }] },
      });
    }
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
  add(`Revenue, FY${year(revenue?.end)}`, revenue && money(revenue.value));
  add(`Net income, FY${year(income?.end)}`, income && money(income.value));
  add("Shares outstanding", shares && count(shares.value));
  if (market) {
    add("Perp volume, 24h", money(market.volume));
    add("Perp open interest", money(market.openInterest));
    // Hyperliquid quotes funding per hour.
    add("Funding, annualized", finite(market.funding) ? pct(market.funding * 24 * 365 * 100, 1) : null);
  }
  if (rows.length > 0) children.push({ layout: "grid", columns: 2, children: rows });

  if (who?.board?.length > 0) children.push(markdown(`**Board**: ${who.board.join(", ")}`));
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
    market && pct(market.change) && `${pct(market.change)} 24h`,
    who?.ceo && `CEO ${who.ceo.name}`,
  );
  return `${line.replace(/[{}]/g, "")}\n\n${JSON.stringify({ layout: "stack", children })}`;
}
