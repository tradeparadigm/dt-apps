---
name: stock
description: Show one US-listed company in full, live - its price, CEO, board, financials and latest SEC filings. Users run it as /stock msft.
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: app_command
command-arg-mode: raw
---

# stock

openclaw runs `scripts/command.mjs` directly, with no model turn. Every figure
is read live on each run.

`/stock msft` shows one company:

- its price from trade.xyz's perp on Hyperliquid, with the last session's
  change (4pm New York close to close) and its move since, a 30-day chart, the perp's volume, open interest and funding, and market cap and P/E
  worked out from the SEC's share count and net income. A stock trade.xyz
  lists no perp on has no price.
- its CEO and chair (with the year each started), board, founding year,
  headquarters and headcount, from Wikidata, found by the company's SEC CIK.
- its industry, exchange, fiscal year end, and its latest annual revenue, net
  income and shares outstanding, from SEC EDGAR.
- its eight latest annual, quarterly, current and proxy reports and insider
  trades, each linked to the filing.
- up to five headlines from the last two days, from Google News' RSS
  search for its name and ticker, one per story, each linked.

The reply is a plain line, then a component spec the terminal draws (its
`features.generativeUi`). A section whose data cannot be read is left out.

As the user types after `/stock `, the terminal runs `scripts/lookup.mjs` with
what they typed and shows the companies whose ticker or name starts with it,
from the SEC's list of tickers, read at most once a day.
