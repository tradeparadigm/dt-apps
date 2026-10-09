---
name: paradex-api
description: >
  Paradex, the Starknet perpetual futures and options exchange: markets, order
  book, account, positions, fills, funding, and placing and cancelling orders.
  Use for ANY Paradex request. The tools are `paradex__*` MCP tools, called by
  exact name. NOT Paradigm: an RFQ or a block trade is paradigm-rfq-trader even
  when it settles on Paradex.
metadata:
  author: tradeparadigm
---

# Paradex

Every Paradex read and write goes through the `paradex` MCP server. The server
signs for you. Do not look for credentials, environment variables or a CLI.
Call a tool with `tool_call`, passing its exact name below as `id` and its
arguments as `args`. You do not need `tool_search` or `tool_describe`.

## Paradex is not Paradigm

Paradex is an exchange with an order book. Paradigm is an institutional RFQ
platform, and Paradex is one of the venues it settles on. An RFQ, a block
trade, a quote or crossing a quote is Paradigm: use `paradigm-rfq-trader`. An
order on the book, a position, a fill or market data is Paradex: you are in
the right place.

## Tools

`?` marks an optional argument.

### Account

- `paradex__paradex_account_summary()`: account value, margin, leverage
- `paradex__paradex_account_balance()`: token balances
- `paradex__paradex_account_positions()`: open positions
- `paradex__paradex_account_overview()`: summary, balances, positions, fees and margin mode in one call
- `paradex__paradex_account_fills(market_id, start_unix_ms, end_unix_ms)`: your executions
- `paradex__paradex_account_funding_payments(market_id?, start_unix_ms, end_unix_ms)`: funding paid and received
- `paradex__paradex_account_transactions(transaction_type?, start_unix_ms, end_unix_ms, limit?)`: deposits, withdrawals, transfers and other activity

### Orders

- `paradex__paradex_open_orders(market_id?, limit?, offset?)`: resting orders
- `paradex__paradex_orders_history(market_id, start_unix_ms, end_unix_ms)`: past orders
- `paradex__paradex_order_status(order_id, client_id)`: one order. Pass `""` for whichever id you do not have
- `paradex__paradex_create_order(market_id, order_side, order_type, size, price, trigger_price, instruction?, reduce_only?, client_id)`: place an order
- `paradex__paradex_cancel_orders(order_id?, client_id?, market_id?)`: cancel one order, or every order in a market
- `paradex__paradex_trade_preview(market_id, side, size, margin_methodology?)`: readiness checks and margin before and after a trade; run it before placing one

`order_side` is `BUY` or `SELL`. `order_type` is one of `MARKET`, `LIMIT`,
`STOP_LIMIT`, `STOP_MARKET`, `TAKE_PROFIT_LIMIT`, `TAKE_PROFIT_MARKET`,
`STOP_LOSS_MARKET`, `STOP_LOSS_LIMIT`. `instruction` is `GTC` (the default),
`POST_ONLY`, `IOC` or `RPI`.

### Market data

- `paradex__paradex_markets(market_ids?, jmespath_filter?, limit?, offset?)`: instruments, tick size, minimum notional
- `paradex__paradex_market_summaries(market_ids?, jmespath_filter?, limit?, offset?)`: mark price, 24h volume, open interest, `underlying_price`
- `paradex__paradex_bbo(market_id)`: best bid and offer
- `paradex__paradex_orderbook(market_id, depth?)`: book depth
- `paradex__paradex_klines(market_id, resolution?, start_unix_ms, end_unix_ms)`: candles; `resolution` is minutes, one of 1, 3, 5, 15, 30, 60
- `paradex__paradex_funding_data(market_id, start_unix_ms, end_unix_ms)`: funding rate history
- `paradex__paradex_trades(market_id, start_unix_ms, end_unix_ms)`: public trades

Market symbols look like `ETH-USD-PERP`. Take exact names from
`paradex__paradex_markets`.

## Venue facts

- Times are unix milliseconds. Run `date +%s%3N` for now before you build a
  window.
- Minimum notional and tick size come from `paradex__paradex_markets`. Check
  them before sizing an order.
- `create_order` needs `trigger_price` as a number. Pass `0` when the order
  type has no trigger. A `MARKET` order takes `price: 0`.
- `client_id` is required on `create_order`. Make one up per order and keep
  it, so `order_status` and `cancel_orders` can find the order.
- Post-only is `instruction: "POST_ONLY"`. Reduce-only is `reduce_only: true`.
- `cancel_orders` with no arguments cancels every open order in every market.
  Name the order or the market unless the user asked for all of it.
- Placing an order needs the signing key. The read-only token reads only.

## When there are no `paradex__` tools

The server did not start for this account. Tell the user which of these
applies, cheapest to check first:

- The change has not reached the agent yet. Ask the user to reload, or retry
  shortly.
- No Paradex credential is enrolled.
- The credential was enrolled with a custom host. Only a credential for one of
  the app's environments (mainnet, testnet, nightly) starts the server.
- The credential is missing its account address, or a signing key is missing
  its public key.
- Another MCP server is already named `paradex`.

## Anything not covered here

Paradex's API reference is at https://docs.paradex.trade. Use it for margin
rules, fee tiers and order semantics.
