---
name: account
title: OKX account
description: Positions (perpetuals, futures, options, margin), open and stop orders, balances and the account summary of one OKX account.
credential: okx-*
params: ENVIRONMENT?
---

# Reading an OKX account's data

posman keeps these as OKX sends them (`fetch.mjs` beside this file is the
adapter): each dataset is the `data` of OKX's answer, pages joined. Every
number is a **string**: parse it before you compare or add. Times are unix
**milliseconds**. Instrument ids say the product: `BTC-USDT` spot or margin,
`BTC-USDT-SWAP` a perpetual, `BTC-USD-261127` a dated future,
`BTC-USD-261225-74000-C` an option.

Track it with the OKX **secret key** credential (`okx-mainnet-secret`); the
API key and the passphrase must be enrolled too, since the proxy attaches
them to every private call. A **demo-trading** key set needs
`--param ENVIRONMENT=demo` (the same host, with `x-simulated-trading: 1`);
the default is live.

**Read posman's rows first** (`posman.mjs show`): the same positions, open
orders and balances in posman's own schema, whose fields mean the same for
every venue. The records below are for anything those rows do not carry.

## posman's rows: how OKX maps to them

- **The instrument** comes from `GET /api/v5/public/instruments`, read per
  instrument family held: `kind` from `instType` (`SWAP` perp, `FUTURES`
  future, `OPTION` option, `MARGIN` and `SPOT` spot), `underlying` and
  `quote_asset` from `uly` (`BTC-USD`), `settle_asset` from `settleCcy`,
  `contract` from `ctType`, and a future's or option's `expiry`, `strike`
  (`stk`) and `option_type` (`optType`). An option's `quote_asset` is USD:
  its strike and greeks are in USD. Without the instruments, positions and
  orders are not mapped that fetch (`_errors.instruments`), so posman keeps
  their last rows. Spot and margin need none: their id is `BASE-QUOTE`.
- **Sizes are in the underlying.** OKX counts SWAP, FUTURES and OPTION in
  contracts, one being `ctVal` × `ctMult` of `ctValCcy`. When `ctValCcy` is
  the underlying (linear contracts, every option: `BTC-USDT-SWAP` is
  0.01 BTC, a `BTC-USD` option 0.01 BTC), `size` is contracts × that. An
  **inverse** contract (`BTC-USD-SWAP`, `BTC-USD-261127`) is USD face value
  (100 USD), so its `size` is contracts × face value ÷ the mark price, in
  BTC, rounded to 12 decimals: what the position is worth in BTC at the mark,
  which moves with the price. An inverse order uses its own price (its
  trigger, for a stop at market). A margin position's `pos` is in the base
  when `posCcy` is the base (a long); when `posCcy` is the quote it is a
  short, whose size is the base borrowed (`liab` in `liabCcy`). In
  long/short mode `posSide` gives the sign; in net mode `pos` carries it.
- **`unrealized_pnl` is `upl`**, which OKX computes from the mark price
  alone: funding is settled every period, into `fundingFee`. It is in `ccy`
  (`pnl_asset`): USDT for a `-USDT` contract, the coin (BTC) for an inverse
  one or an option.
- **`funding_pnl` is `fundingFee`**: the funding the position has paid or
  received since it was opened, positive when received (OKX adds it into
  `realizedPnl`). Null when OKX leaves it empty (futures, margin).
- **`funding_rate`** comes from `GET /api/v5/public/funding-rate?instId=ANY`:
  the current period's `fundingRate` divided by the period's hours
  (`nextFundingTime − fundingTime`: 8, or 4, 2, 1 on some perpetuals);
  positive means longs pay. Perpetuals only. Without it that fetch, null
  (`_errors.funding_rates`).
- **Greeks.** A perpetual's, future's or margin position's `delta` is its
  size and its `gamma`, `vega` and `theta` 0. An option's come from
  `GET /api/v5/public/opt-summary`: OKX's Black-Scholes greeks (`deltaBS`,
  `gammaBS`, `vegaBS`, `thetaBS`: per 1 unit of the underlying, in USD, vega
  per vol point, theta per day) times the signed size, and `iv` from
  `markVol`. OKX's coin greeks (`delta`, ... without `BS`: premium-adjusted,
  in BTC) are not used. Without the summary, an option's greeks are null
  (`_errors.option_summary`).
- **An option's prices are null.** OKX quotes an option's premium in its
  coin (`avgPx`, `markPx` in BTC), not in the USD its strike is in; read
  them in `positions`. An option order's `price` is OKX's own USD price of
  it (`pxUsd`).
- **`liquidation_price`** is `liqPx`, null when OKX gives none (an option,
  or no level). `margin_mode` is `mgnMode`, `portfolio` for a cross position
  in a portfolio-margin account (`acctLv` 4). `leverage` is `lever`.
- **Orders** (`open_orders`): `type` from `ordType` (`limit`, `post_only`,
  `fok`, `ioc` are limits with that time in force; `market` and
  `optimal_limit_ioc` market), `status` `open` or `partially_filled`,
  `filled_size` from `accFillSz`, `reduce_only` from `reduceOnly`.
- **Algo orders** (`algo_orders`) are `untriggered` rows: a `conditional`
  with only a stop-loss is a `stop_market` or `stop_limit` (`slTriggerPx`,
  `slOrdPx`, `-1` meaning at market), with only a take-profit a
  `take_profit_*`; an `oco` is `other` (both prices are in the record); a
  `trigger` order is `other` with its `triggerPx` and `ordPx`; a
  `move_order_stop` is a `trailing_stop` at `moveTriggerPx`.
- **Not mapped** (raw only, named on the adapter's stderr): a stop or
  take-profit on a whole position (`closeFraction`, no `sz`), a spot market
  order sized in the quote currency (`tgtCcy` `quote_ccy`), an inverse order
  with no price, and `EVENTS` contracts.
- **Balances**: `asset` from `ccy`, `total` from `cashBal` (`eq` when OKX
  leaves it empty): the cash balance, negative when borrowed. Equity with
  unrealized PnL is `eq` in `balances`.

## `positions`: open positions

A list, one record per position (`GET /api/v5/account/positions`).

| Field | Is |
|---|---|
| `instType` | `SWAP`, `FUTURES`, `OPTION`, `MARGIN` (or `EVENTS`) |
| `instId` | the instrument |
| `posSide` | `net` (sign in `pos`), or `long` / `short` in long/short mode (`pos` positive) |
| `pos` | the position: **contracts** for SWAP, FUTURES and OPTION; for MARGIN an amount of `posCcy` |
| `posCcy` | MARGIN only: the base currency for a long, the quote for a short |
| `avgPx` | average entry price: the quote currency (USD for inverse); the coin for an option |
| `markPx`, `last`, `idxPx` | mark, last traded and index price |
| `upl` | unrealized PnL at the mark, in `ccy`, funding excluded |
| `fundingFee` | funding paid or received since the position opened |
| `realizedPnl` | `pnl + fee + fundingFee + liqPenalty + settledPnl` |
| `liqPx` | estimated liquidation price; empty when there is none |
| `lever`, `mgnMode` | leverage, `cross` or `isolated` |
| `imr`, `mmr`, `mgnRatio`, `margin` | margin requirements (USD), the maintenance margin ratio, an isolated position's margin |
| `liab`, `liabCcy` | MARGIN only: what is borrowed (negative) |
| `notionalUsd` | notional in USD |
| `deltaBS` ... `vegaPA` | an option's greeks: `BS` in USD, `PA` in the coin |
| `ccy` | the margin currency, which `upl` is in |
| `cTime`, `uTime` | milliseconds |

## `open_orders`: resting orders

A list, one record per order on the book (`GET /api/v5/trade/orders-pending`).

| Field | Is |
|---|---|
| `ordId`, `clOrdId` | OKX's id and the one it was placed with |
| `instType`, `instId` | the instrument |
| `side` | `buy` or `sell`; `posSide` in long/short mode |
| `ordType` | `limit`, `market`, `post_only`, `fok`, `ioc`, `optimal_limit_ioc`, `mmp`, `op_fok`, ... |
| `px` | the limit price (the coin, for an option); `pxUsd`, `pxVol` an option's in USD and as IV |
| `sz`, `accFillSz` | the size and what has filled: **contracts** for SWAP, FUTURES, OPTION; spot in the base (the quote for a market buy with `tgtCcy` `quote_ccy`) |
| `state` | `live` or `partially_filled` |
| `reduceOnly` | `"true"` for a reduce-only order |
| `tdMode` | `cash`, `cross` or `isolated` |
| `cTime`, `uTime` | milliseconds |

## `algo_orders`: stop, take-profit, trigger and trailing orders

A list (`GET /api/v5/trade/orders-algo-pending`, `ordType` `conditional,oco`,
then `trigger`, then `move_order_stop`).

| Field | Is |
|---|---|
| `algoId`, `algoClOrdId` | OKX's id (what a cancel takes) and the client's |
| `ordType` | `conditional`, `oco`, `trigger` or `move_order_stop` |
| `side`, `instId` | as for an order |
| `sz` | the size (contracts for derivatives); empty with `closeFraction` `1`: the whole position |
| `slTriggerPx`, `slOrdPx` | stop-loss trigger and order price (`-1` at market) |
| `tpTriggerPx`, `tpOrdPx` | take-profit trigger and order price |
| `triggerPx`, `ordPx` | a trigger order's trigger and price |
| `moveTriggerPx`, `callbackRatio`, `callbackSpread`, `activePx` | a trailing stop's current trigger, distance and activation |
| `state` | `live` or `pause` |
| `reduceOnly` | true for reduce-only |
| `cTime` | milliseconds |

## `balances`: currency balances

A list, one record per currency held (`details` of
`GET /api/v5/account/balance`; a currency with zero cash and equity is left
out by OKX).

| Field | Is |
|---|---|
| `ccy` | the currency |
| `cashBal` | cash balance |
| `eq`, `eqUsd` | equity in the currency (unrealized PnL included), and in USD |
| `availBal`, `frozenBal`, `ordFrozen` | available, frozen, and frozen for open orders |
| `liab`, `interest` | borrowed (positive) and interest owed |
| `upl` | unrealized PnL of positions in this currency |
| `uTime` | milliseconds |

## `account`: the account summary

One object, the rest of the balance answer, in USD:

| Field | Is |
|---|---|
| `totalEq` | total equity |
| `adjEq` | equity after haircuts: what margins the account (multi-currency and portfolio margin) |
| `imr`, `mmr` | initial and maintenance margin required |
| `mgnRatio` | `adjEq / mmr`; at or below 1 the account is being liquidated |
| `notionalUsd` | gross notional of the derivative positions |
| `upl` | unrealized PnL of the cross positions |
| `uTime` | milliseconds |

Fields that do not apply to the account's mode are empty strings.

## `account_config`: how the account is set up

One object (`GET /api/v5/account/config`): `acctLv` (`1` spot, `2` futures
mode, `3` multi-currency margin, `4` portfolio margin), `posMode`
(`net_mode` or `long_short_mode`), `uid`, and OKX's other settings.

## When the account reads refused, partial or empty

The adapter exits 3 (`auth_failed`, "Credential refused") when OKX or the
proxy refuses the credential: an HTTP 401 or 403, or one of OKX's credential
codes in a 200. OKX's code is in the error. Every cause, cheapest first:

| OKX says | Causes |
|---|---|
| `50103` / `50104` header `OK-ACCESS-KEY` / `-PASSPHRASE` empty | that credential is not enrolled (the proxy attaches only what is); the request went to a host or path outside the credentials' routes, so the proxy added nothing |
| `50111` invalid key, `50105` passphrase incorrect, `50119` key does not exist | the wrong value enrolled; the key deleted at OKX; a live and a demo set both enrolled, which break each other |
| `50113` invalid signature | the secret enrolled belongs to another key; the proxy substituted nothing (host or path outside the routes); the signed bytes differ from the request (an adapter bug) |
| `50101` key does not match the environment | a demo-trading key without `--param ENVIRONMENT=demo`, or a live key with it |
| `50102` / `50112` timestamp | the pod's clock is more than 30 s off OKX's |
| `50110`, `50121`, `50035` IP | the key is bound to IPs that do not include the agent's egress address |
| `50120`, `50030` permission | the key lacks Read permission |
| `50100` API frozen | OKX froze the key: the user must see OKX's support |
| a 403 from the DIME credential proxy | its plain-text body says what is missing (`placeholder_absent`: no placeholder where the secret's route wants one) |

`credential_missing` means the secret is not among the agent's credentials:
enrol it in Settings → Credentials. A dataset that fails otherwise (a 5xx, a
rate limit, `50011`) is named in `_errors` and keeps its last copy; the rest
are written.
