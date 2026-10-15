---
name: bybit-api
description: >
  Bybit, the centralised exchange: balances, positions, orders, fills,
  funding, market data, option chains and greeks, and placing, amending and
  cancelling orders on perpetuals, futures and options. Use for
  ANY Bybit request. The tools are `bybit__*` MCP tools, called by exact name.
metadata:
  author: tradeparadigm
---

# Bybit

Every Bybit read and write goes through the `bybit` MCP server, which signs
for you. Do not look for credentials, environment variables or a client, and
do not build HTTP requests. Call a tool with `tool_call`, passing its exact
name below as `id` and its arguments as `args`. You do not need `tool_search`
or `tool_describe`.

`?` marks an optional argument.

## Account

- `bybit__get_balance()`: total, free, used and unrealised PnL, per coin
- `bybit__get_positions(symbols?)`: open positions
- `bybit__get_fills(symbol, orderId?, from?, to?, limit?)`: your executions on one market. Without `symbol` it returns nothing: when the user names no market, take the symbols from `bybit__get_positions` or `bybit__get_order_history` and ask per symbol
- `bybit__get_closed_pnl(symbol, from?, to?, limit?)`: realised PnL of closed positions
- `bybit__get_income(symbol?, incomeType?, startTime?, endTime?, limit?)`: funding, fees and realised PnL records. `incomeType` is `FUNDING_FEE`, `COMMISSION` or `REALIZED_PNL`; any other value returns nothing. For funding paid, pass `incomeType: "FUNDING_FEE"` and a `startTime`

## Orders

- `bybit__get_open_orders(symbols?)`: resting and conditional orders
- `bybit__get_order_history(symbol?, orderId?, from?, to?, limit?)`: past orders
- `bybit__place_order(symbol, side, type, amount, price?, triggerPrice?, triggerType?, stopLoss?, takeProfit?, reduceOnly?, timeInForce?, clientOrderId?, impliedVolatility?)`: returns the new order ids
- `bybit__amend_order(orderId, price?, amount?)`: exactly one of `price` and `amount`
- `bybit__cancel_orders(orderIds)`: cancel by id
- `bybit__cancel_all_orders(symbol?)`: every open order, or every open order on one market
- `bybit__set_leverage(symbol, leverage)`
- `bybit__set_position_mode(hedged)`
- `bybit__set_margin_type(isolated)`: switches the whole account between isolated and cross margin, for every market

`side` is `buy` or `sell`. `type` is `limit`, `market`, `conditional`,
`stop_market`, `take_profit_market` or `trailing_stop_market`.
`timeInForce` is `GoodTillCancel`, `ImmediateOrCancel`, `FillOrKill` or
`PostOnly`. `triggerType` is `mark_price`, `last_price` or `index_price`.

## Market data

- `bybit__get_markets(symbols?, search?)`: tick size, size step, minimum size and maximum leverage. With no argument, symbols only
- `bybit__get_tickers(symbols?)`: last, mark, index, bid, ask, 24h volume, open interest and funding rate
- `bybit__get_candles(symbol, interval, from?, to?, limit?)`: `interval` is `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `12h`, `1d` or `1w`

## Options

- `bybit__get_option_expiries(underlying)`: expiries as ms and ISO dates
- `bybit__get_option_chain(underlying, expiry?, right?, strikeMin?, strikeMax?)`: bid, ask, mark, IVs, underlying price and greeks per option, with `units` for the greeks. `right` is `call` or `put`. `expiry` is the millisecond `expiry` from `get_option_expiries`, never the date. Pass a strike range: one underlying has hundreds of options
- `bybit__get_account_greeks()`: delta, gamma, vega and theta summed per underlying

Option positions, orders, fills and cancels use the tools above.

- Take option symbols, such as `BTC-30OCT26-96000-C-USDT`, from the chain.
  Underlyings include BTC, ETH, SOL and some stocks.
- Every option Bybit lists is quoted and settled in USDT.
- An option order is `limit` or `market`, priced in USDT premium. Triggers,
  `stopLoss` and `takeProfit` are refused. To price by implied volatility,
  pass `impliedVolatility` as a fraction (`0.5` is 50%) and leave `price` out.
- BTC options tick in 5 USDT, with a 5 USDT minimum price and a 0.01 minimum
  size. `bybit__get_markets(symbols: [<option symbol>])` gives any option's
  tick, step and minimums; `get_markets` without symbols lists no options.
- IVs are fractions. Greeks are Black-Scholes per 1 unit of the underlying in
  USDT: vega per 1 vol point, theta per calendar day.
- Option positions report `leverage` as 1 and `liquidationPrice` as 0. Closed PnL,
  leverage and position mode do not exist for options.
- Crypto options expire and settle at 08:00 UTC, and stock options at 20:00
  or 21:00 UTC. Take the exact time from `bybit__get_option_expiries`.
- Demo trading has no option tools. Tell the user options are not available
  on demo.

## Facts

- The tools cover the markets `bybit__get_markets` lists. Take exact symbols
  from it, such as `BTCUSDT` or `BTCPERP`; never build one by hand.
- `amount` is in the base coin: `0.001` on `BTCUSDT` is 0.001 BTC. The
  minimum and step are `limits.amount.min` and `precision.amount`, and
  `precision.price` is the tick.
- Time arguments are unix milliseconds. Run `date +%s%3N` for now. In
  results, `timestamp` on fills and candles is unix seconds, and on income
  it is milliseconds.
- Post-only is `timeInForce: "PostOnly"`. Reduce-only is `reduceOnly: true`.
- `bybit__cancel_all_orders()` with no symbol cancels every open order. Name
  the market unless the user asked for all of it.
- The environment comes from the enrolled credential. Mainnet is real money.

## Not covered yet

Spot, inverse contracts, transfers, deposits, withdrawals and private
WebSocket streams. Tell the user Bybit's tools do not cover them yet; do not
try to reach them another way. Options are covered on mainnet and testnet,
not on demo.

## When there are no `bybit__` tools

The server did not start for this account. Tell the user which of these
applies, cheapest to check first:

- The change has not reached the agent yet. Ask the user to reload, or retry
  shortly.
- No Bybit credential is enrolled.
- The credential's API key is wrong, revoked, or for another environment.
  The server exits at startup when Bybit rejects the key, so its tools
  disappear. Ask the user to check the key on Bybit's API management page and
  re-enrol it.
- The credential was enrolled with a custom host. Only a credential for one
  of the app's environments (mainnet, testnet, demo) starts the server.
- The credential is missing its API key.
- Another MCP server is already named `bybit`.

## When a tool fails

The error text is Bybit's or the credential proxy's. Each symptom below lists
every cause we know, cheapest to check first.

- **`10003` API key is invalid, or an HTTP 401 with an empty body**: the API
  key in the credential is wrong or was revoked on Bybit, or it belongs to a
  different environment from the credential. The same bad key gives `10003`
  on some calls and an empty 401 on others.
- **`10004` error sign**: the enrolled secret does not belong to the API key,
  or the call went to a path outside the credential's routes, so the proxy
  never signed it.
- **`10005` permission denied**: the key lacks the permission the call needs,
  such as trading, or is restricted to IP addresses that exclude the agent.
- **HTTP 403 with a plain-text body**: the credential proxy refused the call
  before it reached Bybit, and the body says why. Report it to the user.
- **Another non-zero code**: Bybit understood the call and disagreed with it,
  for example size below the minimum or insufficient margin. Read the message.
  Some codes, such as `110043` leverage not modified, are informational.

For the first three, ask the user to check the key on Bybit's API management
page and re-enrol it. You cannot see or fix the secret.

For options:

- **`bybit__` tools but no `get_option_*` tools, or "Options are not enabled
  on this Bybit account"**: the account is on demo, which has no options, or
  the agent is still running the previous version of the app. Ask the user to
  reload; on demo, options are not available.
- **`10005` on an option call**: the key lacks Bybit's options trading
  permission, or the account is not a Unified Trading Account.
- **"Bybit options do not support ..." or "limit and market orders only"**:
  the order used a trigger, a stop loss, a take profit or another order type.
  Place a plain limit or market order.
- **"A limit option order needs a price or an impliedVolatility"**: pass one
  of the two.
