---
name: paradex-block-analyst
description: >
  Analysis of one block trade from Paradex's Option Monitor, the cross-venue
  block tape (Paradex, Deribit, Derive, OKX, Bullish). Invoked as
  `/paradex_analyze <VENUE:block_id> <name>`, which the Option Monitor's
  Analyze button sends. Resolves the block from Paradex's public options tape,
  benchmarks the fill against the mark on the venue it settled on, computes the
  net greeks, counts the same structure on the tape over the last week, and
  shows the live quotes. Use when the user runs `/paradex_analyze`, pastes an
  options-tape block, or asks to analyse or get market colour on a block from
  the Option Monitor. A fill the user made belongs to paradex-trade-analyst, a
  structure they are considering to paradex-structure-analyst, and a Paradigm
  RFQ id (r_…) to paradigm-block-analyst.
compatibility: >
  Needs `uv` and network access to Paradex's public options tape and each
  venue's public market data. No credential. Says in the block what it could
  not fetch, never fabricating a fill.
metadata:
  author: tradeparadigm
  version: "1.0"
---

# Paradex block analyst

Benchmarks one block from the Option Monitor against the market it settled on.

## Input

`/paradex_analyze <VENUE:block_id> <name>`. The first token is the block's
venue code and id (`DBT:BLOCK-302780`, `PRDX:1791942177030201709226870000`,
`DRV:01a1388e-…`) and is the only thing that resolves the block. The name after
it is the label the user saw on the Option Monitor row: never build legs,
instruments or the structure from it.

The terminal attaches the block the user clicked, and the tape beside it, as
JSON. Pass it to the script.

## Run one script, relay its output

```bash
bash scripts/analyze.sh <VENUE:block_id> --fill-json - <<'EOF'
<the attached JSON exactly as it arrived>
EOF
```

With nothing attached, drop `--fill-json` and the heredoc: the script reads
the block off the tape itself.

| exit | what it means | what you do |
|---|---|---|
| `0` | the answer: a block, or a one-line `analyze:` message (not found, bad reference, tape down) | relay stdout verbatim as your entire reply |
| `1` | it needs you: stdout ends with a `## For the agent` section | do exactly that section's task, with the data it carries |
| anything else | the script itself did not run | build the analysis by hand, below |

A block on exit `0` already says what it could not settle in a line under its
table. Relay those as printed.

## What the block shows

- **Header**: coin, expiries, strikes, structure, package size (the smallest
  option leg is one package), Buyer or Seller, what one package cost or paid,
  and the fill against the mark.
- **Greeks**: net Δ (coin), vega and theta (USD), Γ, as Σ sign × ratio × leg
  greek × package size. Every leg on the tape carries the taker's side, so the
  signs are the taker's.
- **Fair**: the package fill against the package mark at the trade, and each
  leg's IV.
  - Deribit and Derive: the mark recorded on the leg's own trade (Deribit's
    trades also carry the IV).
  - Paradex and OKX: the close of the venue's one-minute mark candle the trade
    printed in.
  - Any other venue: the same option's live Deribit mark, in USD.
  - A leg with no mark at the trade moves the whole package to the live mark,
    and the block says so. IVs other than Deribit's are live.
  - Coin-quoted fills (Deribit BTC/ETH, OKX) in bps, dollar fills in %.
- **History**: other blocks of the same structure (coin, types, strikes,
  expiries, any size or side) on the options tape over the last 7 days, by
  venue. The tape keeps 7 days.
- **Live**: each leg's bid/ask on the venue it was benchmarked on.

## By hand

Only when the script cannot run. Find the block on
`GET https://api.prod.paradex.trade/v1/options-tape` (public; `start_time` and
`end_time` within the last 168h, `underlying`, `page_size` up to 1000, cursor
in `next`) by `venue` and `block_id`, then follow "What the block shows" with
each venue's public ticker. Say the analysis was built by hand.
