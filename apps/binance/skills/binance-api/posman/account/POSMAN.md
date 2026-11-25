---
name: account
title: Binance account
description: Binance spot balances and open orders, or USD-M futures positions, open orders (stops included), balances and margin, for one signing key.
credential: binance-*
params: ENVIRONMENT?
---

# Reading a Binance account's data

posman keeps these as Binance sends them (`fetch.mjs` beside this file is the
adapter). Every number is a **string**: parse it before you compare or add.
Times are unix **milliseconds**. Symbols are Binance's: `BTCUSDT` (a
perpetual), `ETHUSDT_260327` (a quarterly future delivering 27 March 2026).

**One credential is one product.** Binance's key pair is enrolled per host, so
`binance-spot-secret` reads spot and `binance-usdm-secret` reads USD-M futures
(and the `-demo` ones their demo twins). Track each as its own account; the
two together are the whole Binance account, and neither reads the other's
data. The environment is the one in the credential's name;
`--param ENVIRONMENT=<spot|usdm|spot-demo|usdm-demo>` overrides it. COIN-M
futures, margin and earn balances are not read.

**Read posman's rows first** (`posman.mjs show`): the same positions, open
orders and balances in posman's own schema, whose fields mean the same for
every venue. The records below are for anything those rows do not carry.

## posman's rows: how Binance maps to them

- **The instrument** comes from the public exchange info (`GET
  /fapi/v1/exchangeInfo`, `GET /api/v3/exchangeInfo?symbols=...`):
  `underlying` from `baseAsset`, `quote_asset` from `quoteAsset`,
  `settle_asset` from `marginAsset`, `contract` linear for every USD-M symbol.
  A `PERPETUAL` (TradFi perpetuals included) is a `perp`; a quarterly or weekly
  contract is a `future` whose `expiry` is its `deliveryDate`. A futures symbol
  the exchange info no longer lists is kind `other`, its `underlying` the
  symbol less its margin asset. Without the exchange info, positions and
  orders are not mapped that fetch (`_errors.exchange_info`), so posman keeps
  their last rows.
- **Spot holds no positions**: a spot account's rows are its balances and
  orders, and its `positions` are `[]`.
- **`unrealized_pnl` is `unRealizedProfit`**, mark against entry, already
  without funding: Binance settles funding into the wallet every interval.
  **`funding_pnl` is null**: Binance gives none per position (the income
  history has it, by time, not by position). In the margin asset (`pnl_asset`).
- **`funding_rate`** is the premium index's `lastFundingRate` divided by the
  symbol's funding interval (`fundingIntervalHours` from `GET
  /fapi/v1/fundingInfo`, 8 hours when it lists none); positive means longs pay.
  Null for a dated future, or when the premium index did not answer
  (`_errors.premium_index`).
- **`leverage` and `margin_mode`** come from the signed `GET
  /fapi/v1/symbolConfig` (`CROSSED` cross, `ISOLATED` isolated); null for a
  symbol it does not list, or when it did not answer (`_errors.symbol_config`).
- **`liquidation_price`** is null where Binance writes `0` (no level).
- **Hedge mode** gives a long and a short row for one symbol; the side is
  `positionSide` there, the sign of `positionAmt` in one-way mode. `delta` is
  the size; `gamma`, `vega` and `theta` are 0.
- **Orders**: `type` from Binance's (`STOP`/`STOP_LOSS_LIMIT` stop_limit,
  `STOP_MARKET`/`STOP_LOSS` stop_market, `TAKE_PROFIT`/`TAKE_PROFIT_LIMIT`
  take_profit_limit, `TAKE_PROFIT_MARKET` take_profit_market,
  `TRAILING_STOP_MARKET` trailing_stop, `LIMIT_MAKER` a post-only limit);
  `status` `partially_filled` for `PARTIALLY_FILLED`, `untriggered` for every
  futures algo order, a spot stop not yet working (`isWorking` false) and a
  spot `PENDING_NEW`, else `open`; `remaining_size` is `origQty − executedQty`;
  `GTX` is post-only; a price of 0 is null.
- **Futures stops are algo orders**: since 2025-12-09 Binance keeps every
  USD-M stop, take-profit and trailing order in its Algo Service, so they are
  in `open_algo_orders`, not `open_orders`, and their `order_id` is the
  `algoId` (cancel with `DELETE /fapi/v1/algoOrder?algoId=`). A
  **close-position** order carries no quantity: its row's `size` is the
  position it would close now, and with no such position it is left out. A
  trailing stop's `trigger_price` is null (it moves); its `activatePrice` and
  `callbackRate` are in the record.
- **Spot orders sized in the quote asset alone** (no `origQty`) have no size in
  the base asset and are left out of the rows.
- **Balances**: spot `total` is `free + locked`; futures `total` is the asset's
  `walletBalance`, without unrealized PnL (that is `marginBalance`).

## Spot

### `balances`

The `balances` list of `GET /api/v3/account` (asked with
`omitZeroBalances=true`), one record per asset held.

| Field | Is |
|---|---|
| `asset` | the asset |
| `free` | what is available |
| `locked` | what open orders hold |

### `account`

The rest of that answer: `makerCommission`, `takerCommission`,
`commissionRates`, `canTrade`, `canWithdraw`, `canDeposit`, `accountType`,
`permissions`, `uid`, `updateTime` (milliseconds).

### `open_orders`

`GET /api/v3/openOrders`, every symbol.

| Field | Is |
|---|---|
| `orderId`, `clientOrderId` | Binance's id and the one the order was placed with |
| `symbol` | the symbol |
| `side` | `BUY` or `SELL` |
| `type` | `LIMIT`, `LIMIT_MAKER`, `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TAKE_PROFIT`, `TAKE_PROFIT_LIMIT` |
| `price`, `stopPrice` | the limit price and the trigger, in the quote asset; `0` when there is none |
| `origQty`, `executedQty` | the order's size and what has filled, in the base asset |
| `status` | `NEW`, `PARTIALLY_FILLED`, `PENDING_NEW` |
| `isWorking` | false for a stop that has not triggered |
| `timeInForce` | `GTC`, `IOC`, `FOK` |
| `time`, `updateTime` | milliseconds |

## USD-M futures

### `positions`

`GET /fapi/v3/positionRisk`, open positions only.

| Field | Is |
|---|---|
| `symbol` | the symbol |
| `positionSide` | `BOTH` (one-way mode), `LONG` or `SHORT` (hedge mode) |
| `positionAmt` | the position, **signed**, in the base asset |
| `entryPrice`, `breakEvenPrice`, `markPrice` | in the quote asset |
| `unRealizedProfit` | unrealized PnL, in `marginAsset` |
| `liquidationPrice` | `0` when there is no level |
| `notional` | the position's value at mark, signed |
| `marginAsset` | what it is margined in |
| `initialMargin`, `maintMargin`, `isolatedMargin`, `isolatedWallet` | margin, in `marginAsset` (isolated ones 0 in cross margin) |
| `adl` | auto-deleverage queue position, 0-4 |
| `updateTime` | milliseconds |

### `open_orders`

`GET /fapi/v1/openOrders`, every symbol: limit and market orders on the book.

| Field | Is |
|---|---|
| `orderId`, `clientOrderId` | Binance's id and the one the order was placed with |
| `symbol`, `side`, `positionSide` | as for positions; `side` is `BUY` or `SELL` |
| `type`, `origType` | `LIMIT`, `MARKET`; what it was placed as |
| `price`, `stopPrice` | in the quote asset; `0` when there is none |
| `origQty`, `executedQty` | in the base asset |
| `status` | `NEW` or `PARTIALLY_FILLED` |
| `timeInForce` | `GTC`, `IOC`, `FOK`, `GTX` (post-only), `GTD`, `RPI` |
| `reduceOnly`, `closePosition` | booleans |
| `goodTillDate` | milliseconds, for `GTD` |
| `time`, `updateTime` | milliseconds |

### `open_algo_orders`

`GET /fapi/v1/openAlgoOrders`: stop, take-profit and trailing orders waiting
to trigger.

| Field | Is |
|---|---|
| `algoId`, `clientAlgoId` | Binance's id (what a cancel takes) and the one it was placed with |
| `algoType` | `CONDITIONAL` |
| `orderType` | `STOP`, `STOP_MARKET`, `TAKE_PROFIT`, `TAKE_PROFIT_MARKET`, `TRAILING_STOP_MARKET` |
| `symbol`, `side`, `positionSide` | as for orders |
| `quantity` | in the base asset; `0` for a close-position order |
| `triggerPrice`, `price` | the trigger, and the limit price of a `STOP`/`TAKE_PROFIT` |
| `workingType` | what the trigger watches: `MARK_PRICE` or `CONTRACT_PRICE` |
| `activatePrice`, `callbackRate` | a trailing stop's activation and its callback, in percent |
| `closePosition`, `reduceOnly`, `priceProtect` | booleans |
| `algoStatus` | `NEW` |
| `createTime`, `updateTime` | milliseconds |

### `balances`

The `assets` list of `GET /fapi/v3/account`, assets with a wallet balance only.

| Field | Is |
|---|---|
| `asset` | the asset |
| `walletBalance` | the balance, realized PnL and funding included |
| `unrealizedProfit` | unrealized PnL of positions margined in it |
| `marginBalance` | `walletBalance + unrealizedProfit` |
| `initialMargin`, `maintMargin` | margin it holds |
| `availableBalance`, `maxWithdrawAmount` | what is free to open positions with, and to withdraw |
| `updateTime` | milliseconds |

### `account`

The rest of `GET /fapi/v3/account`, the account's totals, in USD terms:
`totalWalletBalance`, `totalUnrealizedProfit`, `totalMarginBalance`,
`totalInitialMargin`, `totalMaintMargin` (near `totalMarginBalance` is near
liquidation), `availableBalance`, `maxWithdrawAmount`, and the cross and
position/open-order parts of the margin.

A field Binance adds later appears in these records too: read the record
rather than assume these are all.

## When a fetch fails

The account's state, then each cause, cheapest to check first.

| State | Causes |
|---|---|
| `auth_failed` (exit 3) | **401 `-2014` API-key format invalid**: no key reached Binance. The api-key credential for this environment was never enrolled (Binance needs it on every signed call), the host is not the one the credential was enrolled for (the proxy then passes the request through untouched), or the path is not in the credential's routes. **401 `-2015` Invalid API-key, IP, or permissions**: the key lacks the permission (futures trading is off on a new key), the key has an IP allowlist without the proxy's egress, or a demo key against a live host (or the reverse). **`-1022` signature not valid**: the secret enrolled is not the key's, or the signed bytes changed after signing. **`-2008`, `-1002`**: the key is unknown or not authorised. **403 in plain text**: the credential proxy refused before Binance (its body says why). |
| `partial`, `open_algo_orders` in the errors | **401 `-2014` on the algo read alone**: the credential was enrolled before binance 1.2.0, whose routes added `/fapi/v1/openAlgoOrders`; enrol the key pair again. The other datasets are still read; futures stops are missing from the rows until then. |
| `partial`, another dataset in the errors | That read failed alone (a 5xx, a timeout, a rate limit); posman keeps its last copy. `exchange_info`: the instruments could not be read, so positions and orders keep their last rows. `premium_index`, `funding_info`, `symbol_config`: funding rate, leverage or margin mode are null this fetch. |
| `error` (exit 1) | **451**: Binance refuses requests from the agent's location. **429 / 418**: rate limited or IP banned (`Retry-After`). A network failure reaching Binance. |
| `error` (exit 2) | No credential on the source, or one whose name says no environment: add `--param ENVIRONMENT=...`. |
