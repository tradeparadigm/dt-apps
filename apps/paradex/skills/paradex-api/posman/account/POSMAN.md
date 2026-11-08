---
name: account
title: Paradex account
description: Positions, open orders, balances and the account summary of one Paradex account.
credential: paradex-*
params: ENVIRONMENT?
---

# Reading a Paradex account's data

posman keeps these as Paradex sends them (`fetch.py` beside this file is the
adapter). Every number is a **string**: parse it before you compare or add.
Times are unix **milliseconds**. Market symbols look like `BTC-USD-PERP` (a
perpetual) and `BTC-USD-27JUN26-70000-C` (an option).

Either Paradex credential works: the read-only token, or the signing key. The
environment (mainnet, testnet, nightly) is the one in the credential's name;
`--param ENVIRONMENT=<env>` overrides it.

**Read posman's rows first** (`posman.mjs show`): the same positions, open
orders and balances in posman's own schema, whose fields mean the same for
every venue. The records below are for anything those rows do not carry.

## posman's rows: how Paradex maps to them

- **The instrument** comes from `GET /v1/markets`: `kind` from `asset_kind`
  (`PERP` perp, `OPTION` and `PERP_OPTION` option, `FUTURE` future),
  `underlying` from `base_currency`, `quote_asset`, `settle_asset` (USDC),
  `contract` linear, and an option's `strike`, `option_type` and `expiry`. A
  perpetual option has no expiry. Without the markets list, positions and
  orders are not mapped that fetch (`_errors.markets`), so posman keeps their
  last rows.
- **`unrealized_pnl` is `unrealized_pnl − unrealized_funding_pnl`**: Paradex's
  own figure includes the running funding, posman's excludes it.
- **`funding_pnl` is `realized_positional_funding_pnl + unrealized_funding_pnl`**:
  everything paid or received since the position opened or flipped (Paradex
  resets both then), positive when received. In USDC (`pnl_asset`).
- **`mark_price`, `funding_rate`, `iv` and the greeks** come from
  `GET /v1/markets/summary`. Paradex's `funding_rate` is per the market's own
  funding period, so posman's is it divided by `funding_period_hours`; positive
  means longs pay. An option's greeks are Paradex's raw greeks (delta per unit,
  gamma per $1 move, vega in USD per 1% of IV, theta in USD per day: Paradex's
  dated-options guide) times the signed size; a perp's delta is its size and
  its gamma, vega and theta 0. Without the summaries those fields are null that
  fetch (`_errors.market_summaries`).
- **`liquidation_price`** is null when Paradex gives an empty one (no level).
- **Orders**: `type` from Paradex's (`STOP_LOSS_*` are stops), `status`
  `untriggered`, `partially_filled` (some of `size` filled) or `open`,
  `filled_size` as `size − remaining_size`, `reduce_only` from `flags`, `RPI`
  as `time_in_force` other.
- **Balances**: `token` and `size`, as Paradex gives them.

## `positions`: open positions

A list, one record per open position (closed ones are left out).

| Field | Is |
|---|---|
| `market` | the market |
| `side` | `LONG` or `SHORT` |
| `size` | the position, **signed**: positive long, negative short, in the market's base asset |
| `average_entry_price`, `average_entry_price_usd` | entry price, in the quote asset and in USD |
| `cost`, `cost_usd` | what the position cost (negative for a short) |
| `unrealized_pnl` | unrealized PnL in the quote asset, funding included |
| `unrealized_funding_pnl` | the funding part of `unrealized_pnl` |
| `realized_positional_pnl` | realized PnL since the position opened or flipped, funding included |
| `liquidation_price` | where it is liquidated; empty when there is no level |
| `leverage` | the position's leverage |
| `status` | `OPEN` |
| `created_at`, `last_updated_at` | milliseconds |

Notional is `abs(size)` times the market's mark price, which is not here: read
it from `paradex__paradex_market_summaries` when you need it.

## `open_orders`: resting orders

A list, one record per order on the book or waiting to trigger.

| Field | Is |
|---|---|
| `id`, `client_id` | Paradex's id and the one the order was placed with |
| `market` | the market |
| `side` | `BUY` or `SELL` |
| `type` | `LIMIT`, `MARKET`, `STOP_LIMIT`, `STOP_MARKET`, `TAKE_PROFIT_*`, `STOP_LOSS_*` |
| `size`, `remaining_size` | the order's size and what is still unfilled, base asset, unsigned |
| `price` | the limit price (`0` for a market order) |
| `trigger_price` | the trigger, for stop and take-profit orders |
| `instruction` | `GTC`, `POST_ONLY`, `IOC` or `RPI` |
| `flags` | `REDUCE_ONLY` among them for a reduce-only order |
| `status` | `NEW`, `UNTRIGGERED` or `OPEN` |
| `created_at` | milliseconds |

## `balances`: token balances

A list, one record per token: `token` (`USDC`, ...), `size` (the balance),
`last_updated_at`.

## `account`: the account summary

One object, the account as a whole, in its settlement asset
(`settlement_asset`, USDC):

| Field | Is |
|---|---|
| `account_value` | the account's value, unrealized PnL included |
| `total_collateral` | collateral |
| `free_collateral` | account value in excess of the initial margin required: what is left to open positions with |
| `initial_margin_requirement` | margin the open positions need |
| `maintenance_margin_requirement` | margin needed to keep them open |
| `margin_cushion` | account value in excess of the maintenance margin; near 0 is near liquidation |
| `status` | `ACTIVE`, or another state such as `LIQUIDATION` |
| `updated_at` | milliseconds |

A field Paradex adds later appears here too: read the record rather than
assume these are all.
