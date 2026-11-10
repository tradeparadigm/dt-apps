---
name: ticker
description: Show a coin's USD price and 24 hour change from CoinGecko. Users run it as /ticker btc eth.
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: app_command
command-arg-mode: raw
---

# ticker

`/ticker <symbol> [symbol ...]` prints each coin's USD price and 24 hour change.
openclaw runs `scripts/command.mjs` directly, with no model turn.
