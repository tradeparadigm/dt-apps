---
name: ticker
description: Show a coin's live USD price and 24 hour change. Users run it as /ticker btc eth.
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: app_command
command-arg-mode: raw
---

# ticker

`/ticker <symbol> [symbol ...]` prints each coin's USD price and 24 hour change.
openclaw runs `scripts/command.mjs` directly, with no model turn.

As the user types after `/ticker `, the terminal runs `scripts/lookup.mjs` with
what they typed and shows the coins it prints as suggestions. It matches them
against every coin CoinMarketCap tracks (its directory: names and ranks, no
prices, read at most once a day), or CoinGecko's top 250 where DIME Terminal's
relay is not available. Prices are always read live.
