---
name: ticker
description: Show the crypto market, a table of coins, or one coin in full, all live. Users run it as /ticker, /ticker btc eth or /ticker btc.
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: app_command
command-arg-mode: raw
---

# ticker

openclaw runs `scripts/command.mjs` directly, with no model turn. Every figure
is read live on each run.

- `/ticker` shows the market: total cap and volume, Fear & Greed, BTC and ETH
  dominance, the altcoin season index, the top-100 and top-20 indexes, and 30
  days of Fear & Greed.
- `/ticker btc eth sol` shows one table row per coin: price, the change today
  (since 00:00 UTC), over 7d and over 30d, rank, market cap and volume.
- `/ticker btc` shows one coin: price, its change today, market cap and
  volume, 30 days of price, 7d to 1y change, its high over the last year, supply, when it
  launched, a line about it with its links, and for a token its contract
  check. Where DefiLlama tracks the coin it adds on-chain figures from
  DefiLlama's free API: for a chain its TVL, stablecoins, DEX volume,
  fees, revenue and market cap over revenue; for a protocol its TVL, fees, revenue and market cap over revenue;
  for a stablecoin its supply and chains. Which chain, protocol or
  stablecoin a coin is comes from DefiLlama's index, read at most once a
  day.

Every change runs from CoinMarketCap's daily closes at 00:00 UTC, where
crypto's day closes on TradingView too. With no closes to read, the reply
shows CoinMarketCap's rolling 24h and periods, and labels the day's change
24h.

The reply is a plain line, then a component spec the terminal draws (its
`features.generativeUi`). A section whose data cannot be read is left out.
Where DIME Terminal's relay is not available (an older agent), `/ticker`
prints prices alone, from CoinGecko's public API.

As the user types after `/ticker `, the terminal runs `scripts/lookup.mjs` with
what they typed and shows the coins it prints as suggestions. It matches them
against every coin CoinMarketCap tracks (its directory: names and ranks, no
prices, read at most once a day), or CoinGecko's top 250 where the relay is
not available.
