---
name: account
title: Bybit account
description: Positions, open orders, balances and the margin summary of one Bybit unified trading account (USDT and USDC perpetuals and futures, inverse contracts, options and spot).
credential: bybit-*
params: ENVIRONMENT?
---

# Reading a Bybit account's data

posman keeps these as Bybit sends them (`fetch.mjs` beside this file is the
adapter), each record with the v5 `category` it was listed under added
(`linear`, `inverse`, `option`, `spot`). Every number is a **string**: parse
it before you compare or add. Times are unix **milliseconds**, as strings.
Symbols look like `BTCUSDT` (USDT perpetual), `BTCPERP` (USDC perpetual),
`BTC-30OCT26` (a dated USDC future), `BTCUSD` (inverse) and
`BTC-30OCT26-96000-C-USDT` (an option).

The credential is the app's API secret, with the API key in its details. The
environment (mainnet, testnet, demo) is the one in the credential's name;
`--param ENVIRONMENT=<env>` overrides it. Demo trading has no options, so on
demo none are asked for.

**Read posman's rows first** (`posman.mjs show`): the same positions, open
orders and balances in posman's own schema, whose fields mean the same for
every venue. The records below are for anything those rows do not carry.

## posman's rows: how Bybit maps to them

- **The instrument** comes from `GET /v5/market/instruments-info`, asked per
  symbol held: `kind` from `contractType` (`*Perpetual` perp, `*Futures`
  future) or the category (`option`, `spot`), `underlying` from `baseCoin`,
  `quote_asset` from `quoteCoin`, `settle_asset` from `settleCoin`,
  `contract` `inverse` for the inverse category and `linear` otherwise (an
  option included), a future's and an option's `expiry` from `deliveryTime`,
  an option's `option_type` from `optionsType` and its `strike` from its
  symbol (`BTC-30OCT26-96000-C-USDT` is 96000). A symbol Bybit no longer
  lists (delisted, settling) is mapped from its name alone, as kind `other`.
  Without the instruments at all, positions and orders are not mapped that
  fetch (`_errors.instruments`), so posman keeps their last rows.
- **`size`** is `size` signed by `side` (`Buy` long, `Sell` short), in the
  base coin. An **inverse** contract counts USD, so its size is Bybit's
  `positionValue` (coins, at the entry price), and an inverse order's size,
  filled and remaining are its USD amounts divided by the order's own price
  (its trigger price when it has none). A spot market order counted in the
  quote coin (`marketUnit: quoteCoin`) is divided the same way.
- **`unrealized_pnl`** is Bybit's `unrealisedPnl` as it is: Bybit's is price
  only, and funding is realised into `curRealisedPnl` as it is paid. In the
  settle coin (`pnl_asset`). **`funding_pnl` is null**: Bybit gives no funding
  total on a position.
- **`liquidation_price`** is null when Bybit gives `""` or `0` (an option, a
  portfolio margin account, no level). **`leverage`** is null for an option
  (Bybit reports `1`, which means nothing there).
- **`margin_mode`** is the account's, from `GET /v5/account/info`'s
  `marginMode` (`REGULAR_MARGIN` cross, `ISOLATED_MARGIN` isolated,
  `PORTFOLIO_MARGIN` portfolio); null when that read failed.
- **`funding_rate`, `iv` and an option's greeks** come from
  `GET /v5/market/tickers`, asked per position held. The funding rate is per
  the market's funding period, so posman's is `fundingRate` divided by
  `fundingIntervalHour` (or the instrument's `fundingInterval` in minutes);
  positive means longs pay. An option's greeks are the ticker's per-unit
  greeks (delta per 1 unit, gamma per 1 USDT, vega per 1 vol point, theta per
  day, in USDT) times the signed size; its `iv` is `markIv`. A perp's or
  future's delta is its size and its gamma, vega and theta 0. Without the
  tickers those fields are null that fetch (`_errors.tickers`). Bybit's own
  position greeks stay in the raw record.
- **Orders**: `type` from `orderType` and `stopOrderType` (`Stop`,
  `StopLoss` and `PartialStopLoss` are stops, `TakeProfit` and
  `PartialTakeProfit` take-profits, `TrailingStop` trailing; a spot
  `tpslOrder`, `OcoOrder` or `BidirectionalTpslOrder` is `other`), so
  `venue_type` is `stopOrderType` when there is one and `orderType`
  otherwise. `status` from `orderStatus` (`New` open, `PartiallyFilled`,
  `Untriggered`); `price` null for a market order; `trigger_price` from
  `triggerPrice`; `filled_size` and `remaining_size` from `cumExecQty` and
  `leavesQty`; `time_in_force` `GTC`, `IOC`, `FOK`, `PostOnly`, and `RPI` as
  other; `reduce_only` from `reduceOnly` (null for spot, which has none).
- **Balances**: one row per coin, `total` its `walletBalance` (negative when
  it is owed).

## When a fetch fails

posman shows the error on the account's card, and `posman.mjs list` names it.
Each symptom lists every cause known, cheapest to check first.

- **Credential refused (exit 3): retCode `10003`, or an HTTP 401 with an
  empty body**: the API key in the credential is wrong or was revoked, or it
  belongs to another environment than the credential's name says (mainnet,
  testnet and demo keys are separate). The same bad key gives `10003` on some
  endpoints and an empty 401 on others.
- **Credential refused: retCode `10004`**: the enrolled secret is not the
  API key's, or the request went to a path outside the credential's routes,
  so the proxy never signed it.
- **Credential refused: retCode `10005`**: the key lacks a read permission
  (positions, orders, wallet) or is not a unified trading account's key.
- **Credential refused: retCode `10010`**: the key is bound to IP addresses
  that exclude the agent's.
- **Credential refused: retCode `10007` or `33004`**: authentication failed,
  or the key has expired. Re-create it on Bybit and re-enrol it.
- **Credential refused: HTTP 403 with a plain-text body**: the credential
  proxy refused the request and the body says why.
- **One dataset's error, not the credential: HTTP 403 mentioning CloudFront
  or a country**: Bybit blocks the region the request came from.
- **One dataset's error: retCode `10002`**: the request's timestamp is outside
  Bybit's 5-second window: the pod's clock is off.
- **One dataset's error: retCode `10006`, `10018` or HTTP 429**: rate limited;
  the next fetch usually succeeds.
- **Set up wrong (exit 2)**: no API key in the credential's details (re-enrol
  it with the key), or an environment the credential's name does not say (add
  `--param ENVIRONMENT=mainnet|testnet|demo`).

## `positions`: open positions

A list, one record per open position, across `linear` (USDT, then USDC),
`inverse` and `option` (empty positions left out).

| Field | Is |
|---|---|
| `category` | `linear`, `inverse` or `option` (added by posman) |
| `symbol` | the market |
| `side` | `Buy` (long) or `Sell` (short) |
| `size` | the position, **unsigned**: base coin, but USD for inverse |
| `positionValue` | its value: quote coin, but coins for inverse |
| `avgPrice` | average entry price |
| `markPrice` | mark price |
| `liqPrice` | liquidation price; `""` when there is none |
| `unrealisedPnl` | unrealised PnL, price only, in the settle coin |
| `curRealisedPnl` | realised PnL of the current position, fees and funding included |
| `cumRealisedPnl` | all-time realised PnL on the symbol (`""` for options) |
| `leverage` | the position's leverage (`""` in portfolio margin) |
| `positionIM`, `positionMM` | initial and maintenance margin |
| `takeProfit`, `stopLoss`, `trailingStop` | the position's TP/SL |
| `delta`, `gamma`, `vega`, `theta` | an option position's greeks, as Bybit computes them |
| `positionIdx` | `0` one-way; `1` and `2` the long and short legs in hedge mode |
| `positionStatus` | `Normal`, `Liq` or `Adl` |
| `createdTime`, `updatedTime` | milliseconds |

## `open_orders`: resting and conditional orders

A list, one record per order, across `linear` (USDT, then USDC), `inverse`,
`option` and `spot`.

| Field | Is |
|---|---|
| `category` | as above (added by posman) |
| `orderId`, `orderLinkId` | Bybit's id and the one the order was placed with |
| `symbol` | the market |
| `side` | `Buy` or `Sell` |
| `orderType` | `Limit` or `Market` (for a conditional order, what it becomes when triggered) |
| `stopOrderType` | `""`, `Stop`, `TakeProfit`, `StopLoss`, `TrailingStop`, `PartialTakeProfit`, `PartialStopLoss`, `tpslOrder`, `OcoOrder`, ... |
| `orderStatus` | `New`, `PartiallyFilled` or `Untriggered` |
| `price`, `qty` | limit price and size (base coin; USD for inverse) |
| `triggerPrice`, `triggerBy` | a conditional order's trigger and the price it watches |
| `cumExecQty`, `leavesQty` | filled and still open |
| `timeInForce` | `GTC`, `IOC`, `FOK`, `PostOnly` or `RPI` |
| `reduceOnly`, `closeOnTrigger` | reduce-only flags |
| `orderIv` | an option order's implied volatility |
| `createdTime`, `updatedTime` | milliseconds |

## `balances`: coins

A list, one record per coin the unified account holds:

| Field | Is |
|---|---|
| `coin` | `USDT`, `BTC`, ... |
| `walletBalance` | the balance |
| `equity` | balance less spot borrowing, plus unrealised PnL and option value |
| `usdValue` | the coin's equity in USD |
| `borrowAmount`, `spotBorrow`, `accruedInterest` | what is borrowed, and its interest |
| `locked` | held by spot orders |
| `totalOrderIM`, `totalPositionIM`, `totalPositionMM` | margin by coin (`""` in portfolio margin) |
| `unrealisedPnl`, `cumRealisedPnl` | PnL by coin |
| `marginCollateral`, `collateralSwitch` | whether the coin counts as collateral |

## `account`: the margin summary

One object, the unified account as a whole, in USD:

| Field | Is |
|---|---|
| `totalEquity` | the account's value |
| `totalWalletBalance` | balances, without unrealised PnL |
| `totalMarginBalance` | wallet balance plus perpetual and future unrealised PnL |
| `totalAvailableBalance` | what is left to open positions with (cross and portfolio margin) |
| `totalInitialMargin`, `totalMaintenanceMargin` | margin the positions and orders need |
| `accountIMRate`, `accountMMRate` | those over the margin balance; an MM rate near 1 is near liquidation |
| `totalPerpUPL` | perpetual and future unrealised PnL |

## `account_info`

`GET /v5/account/info`: `marginMode` (`REGULAR_MARGIN`, `ISOLATED_MARGIN`,
`PORTFOLIO_MARGIN`), `unifiedMarginStatus`, `spotHedgingStatus`,
`updatedTime`.

A field Bybit adds later appears in these records too: read the record rather
than assume these are all.
