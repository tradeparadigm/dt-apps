---
name: paradex-trade-analyst
description: >
  Analysis of a filled Paradex perpetual trade against live Paradex market
  data. Invoked as `/paradex_trade_analyst <fill_id> <market side size price>`,
  which the trade history sends when a user asks to analyse a fill. Resolves
  the fill from the Paradex REST API via the
  paradex-api skill, benchmarks the fill price against the mark and the book
  at the time, reports the position the fill left behind, and states funding
  paid or received over the holding period. Use when the user asks to analyse,
  benchmark or get market colour on a Paradex fill, or pastes Paradex fill
  JSON. Paradigm RFQ blocks belong to paradigm-block-analyst.
compatibility: >
  Needs the paradex-api skill for authenticated reads, which means a Paradex
  credential enrolled through the DIME credential proxy. Public market data
  needs no credential. Reports what it could not read rather than estimating.
metadata:
  author: tradeparadigm
  version: "1.0"
---

# Paradex trade analyst

Benchmarks one filled Paradex trade against the market around it.

## Input

`/paradex_trade_analyst <fill_id> <market side size price>`. The first token
is the fill id and is what resolves the trade. Everything after it is the
label the user saw in the trade history, useful for reading back but never
authoritative.

A message may also carry hidden context holding the source fills as JSON.
When it does, those rows are the fill and no lookup is needed.

## Step 1 — resolve the fill

With hidden context, use it. That is the normal path: the trade history sends
the whole row.

Without it, search for the fill. `GET /v1/fills` takes `asset_kind`, `cursor`,
`end_at`, `market`, `page_size` and `start_at`, and nothing else. There is no
fill-id filter and no single-fill endpoint, so an `id` query parameter is
ignored and the first page comes back looking like an answer.

So: read `GET /v1/fills` through the `paradex-api` skill, which holds the
signing and the host rules, with `market` set to the label's market and
`page_size=100`. Walk `cursor` back through pages and compare each row's `id`
against the fill id as strings. Stop at the exact match, and stop after ten
pages without one.

Report and stop when no row matches. Never read a fill from the label, and
never accept a row whose `id` differs from the one asked for.

## Step 2 — value it

- Compare the fill price against the mark. `/v1/markets/summary` is a live
  snapshot, so it answers for a recent fill and not for an old one. For
  anything older, read `/v1/markets/klines` around the fill's timestamp and
  say which of the two the comparison came from.
- Compare against the book at that moment where the data reaches back far
  enough, and say so when it does not.
- State the difference in basis points of the mark, signed against the side:
  a buy above the mark and a sell below it both read as paying away.

## Step 3 — the position it left

Aggregate the fills of that market around the trade to say what position the
user held after it, and at what average entry. State it in contracts and in
notional.

## Step 4 — funding

Sum funding over the holding period for that market and state it against the
realised move, so a profitable-looking hold that paid it all away in funding
reads as one.

## Output

Lead with the numbers. Fill against mark in basis points, size, notional, the
resulting position, then funding. Say what was unavailable rather than
filling it in.

Never fabricate a mark, a book level or a funding number. A missing venue
read is a missing line, and the line says which read failed.
