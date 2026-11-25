---
name: account
title: Deribit account
description: Positions, open orders and per-currency account summaries (balance, equity, margin) of one Deribit account, every currency and kind.
credential: deribit-*
params: ENVIRONMENT?
---

# Reading a Deribit account's data

posman keeps these as Deribit sends them (`fetch.mjs` beside this file is the
adapter). Every number is a JSON **number**, exactly as Deribit writes it.
Times are unix **milliseconds**. Instrument names are Deribit's own:
`BTC-PERPETUAL` (a perpetual), `BTC-27MAR26` (a dated future),
`BTC-27MAR26-80000-C` (an option), `BTC_USDC-PERPETUAL` (a USDC-margined
perpetual), `BTC_USDC` (spot).

The account is the API key's: the credential is its client secret, the client
id its public detail. The adapter exchanges them for an access token once per
fetch (`public/auth`, the placeholder in the query string, as the deribit-api
skill does) and reads with that token. The environment (mainnet, testnet) is
the one in the credential's name; `--param ENVIRONMENT=<env>` overrides it.

**Read posman's rows first** (`posman.mjs show`): the same positions, open
orders and balances in posman's own schema, whose fields mean the same for
every venue. The records below are for anything those rows do not carry.

## posman's rows: how Deribit maps to them

- **The instrument** comes from `public/get_instrument`, once per instrument
  held or ordered: `kind` (`future` with `settlement_period: perpetual` is a
  perp, other futures dated, `option`, `spot`), `underlying` from
  `base_currency`, `settle_asset` from `settlement_currency`, `contract`
  `inverse` for Deribit's `reversed` (the coin-margined BTC-, ETH- books) and
  `linear` for the USDC ones, and an option's `strike`, `option_type` and
  `expiry`. If one cannot be read, positions or orders naming it are not
  mapped that fetch (`_errors.instruments`), so posman keeps their last rows.
- **Sizes are in the coin.** A future's (perpetuals included) `size` is in USD
  on an inverse book; posman's is `size_currency`, which Deribit gives in the
  coin for every future. An option's `size` is in the coin already. The sign
  is `direction` (`sell` negative); `zero` is no position, and no row.
- **A coin-margined option's rows are in USD**, the asset its strike and
  greeks are in (`quote_asset` `USD`). Deribit prices it in the coin, so
  `entry_price` is `average_price_usd` (Deribit's own), and `mark_price` is
  `mark_price` (in the coin) times the position's `index_price`, exact. A
  USDC option's rows are in USDC, as Deribit gives them.
- **`unrealized_pnl` is `total_profit_loss`**: the position's PnL against its
  average price since it was opened (not the session's `floating_profit_loss`),
  in `settlement_currency` (`pnl_asset`: BTC on an inverse book, USDC on a
  linear one). A perpetual's funding is paid into the session's realized PnL
  (`realized_funding`), not into this figure.
- **`funding_pnl` is null**: Deribit gives only the current session's funding
  on a position (`realized_funding`), not funding since it opened.
- **`funding_rate`** is the perpetual's `funding_8h` (from `public/ticker`)
  divided by 8, exactly; positive means longs pay. **`iv`** is an option's
  `mark_iv` (a percentage) as a fraction. Without the ticker both are null
  that fetch (`_errors.tickers`).
- **Greeks**: an option's `delta`, `gamma`, `vega` and `theta` are the
  position's own (already times its size): delta in the coin, gamma per USD,
  vega in USD per volatility point, theta in USD per day. A future's `delta`
  is its size and its other greeks 0.
- **`liquidation_price` is null**: Deribit no longer computes one
  (`estimated_liquidation_price` is always null). **`margin_mode` is null**:
  Deribit's margin model is the account's (`margin_model` in `balances`),
  not the position's.
- **Orders**: `type` from `order_type` (`take_limit`, `take_market` are
  take-profits), `status` `untriggered` for a trigger order not yet
  triggered, `partially_filled` once `filled_amount` is above 0, else `open`;
  `time_in_force` `post_only` for a post-only order, else from Deribit's
  (`good_til_day` is `gtd`); `client_order_id` is the `label`.
- **An inverse future's order is sized in USD** (`amount`), with no size in
  the coin: posman's `size`, `filled_size` and `remaining_size` are those USD
  amounts over the order's price (its limit, else its trigger), to 10 decimal
  places. That is the coin it buys at that price, not a figure Deribit gives:
  read `amount` for the exact order. An option's and a linear book's `amount`
  is in the coin already.
- **A coin-margined option's order price** is its limit (in the coin) times
  the index price this fetch read (`public/ticker`), so it moves with the
  index: read `price` for the order's own. Null without the ticker.
- **Combo orders** (`future_combo`, `option_combo`) have no row: a combo is
  several instruments, which posman's schema does not hold. Read them here.
- **Balances**: one row per currency whose cash `balance` is not 0
  (`total`). Equity, margin and the greeks totals are in `balances` below.

## When something fails

| Symptom | Causes, cheapest to check first |
|---|---|
| `auth_failed`, `13004 invalid_credentials` | the client id in the credential's details is not the key's (re-enrol with the client id Deribit shows beside the key); the key was deleted or disabled on Deribit; the client secret enrolled is not that key's; a mainnet key used against testnet or the other way (`--param ENVIRONMENT`) |
| `auth_failed`, `HTTP 403` at `public/auth` | the credential proxy found no placeholder to swap or refused the credential: the credential is not this app's, or was revoked in DIME |
| `auth_failed`, every read `13009 unauthorized` | the token Deribit just minted was refused: the key's IP allowlist on Deribit excludes the agent, or the key was disabled between the two calls |
| `partial`, one dataset `refused: ... 13021 forbidden` | the key lacks that read's scope: `trade:read` for positions and orders, `account:read` for balances (edit the key on Deribit) |
| `partial`, `_errors.instruments` | an instrument lookup failed (Deribit busy, `10028 too_many_requests`), or more than 200 instruments held and ordered |
| `partial`, `_errors.tickers` | a ticker lookup failed: funding, IV and coin-margined option order prices are null this fetch |
| `error`, `10028 too_many_requests` | the account's request rate is spent: lengthen the interval |
| `error`, `10041 settlement_in_progress` | Deribit's daily settlement (08:00 UTC): the next fetch reads it |

## `positions`: open positions

A list, one record per position, every currency and kind
(`private/get_positions?currency=any`). A position closed in this session
may still be listed, with `direction` `zero` and size 0.

| Field | Is |
|---|---|
| `instrument_name` | the instrument |
| `kind` | `future` (perpetuals included) or `option` |
| `direction` | `buy` (long), `sell` (short) or `zero` |
| `size` | the position, **signed**: in **USD** for an inverse future, in the coin for an option or a linear future |
| `size_currency` | a future's position in the coin, signed |
| `average_price` | entry price: USD for an inverse future, the coin for an inverse option, USDC on a linear book |
| `average_price_usd` | an option's entry price in USD |
| `mark_price` | Deribit's mark, in the same unit as `average_price` |
| `index_price` | the index the instrument settles against |
| `total_profit_loss` | PnL against the average price since the position opened, in the settlement currency |
| `floating_profit_loss`, `floating_profit_loss_usd` | unrealized PnL this session (since the last 08:00 UTC settlement) |
| `realized_profit_loss` | realized PnL this session |
| `realized_funding` | a perpetual's funding this session, included in `realized_profit_loss` |
| `delta`, `gamma`, `vega`, `theta` | the position's greeks (options; a future's `delta` is its coin size) |
| `initial_margin`, `maintenance_margin` | margin the position needs, in the settlement currency |
| `leverage` | a future's current leverage |
| `settlement_price` | the last settlement price (0 before the first) |

## `open_orders`: resting orders

A list, one record per order on the book or waiting to trigger, every
currency and kind (`private/get_open_orders`).

| Field | Is |
|---|---|
| `order_id`, `label` | Deribit's id (what a cancel takes) and the one the order was placed with |
| `instrument_name` | the instrument |
| `direction` | `buy` or `sell` |
| `order_type` | `limit`, `market`, `stop_limit`, `stop_market`, `take_limit`, `take_market`, `trailing_stop` |
| `order_state` | `open` or `untriggered` |
| `amount` | the order's size: **USD** for an inverse future, the coin for an option or a linear book |
| `filled_amount` | how much has filled, in the same unit |
| `price` | the limit (USD for an inverse future, the coin for an inverse option), or `market_price` for a trigger market order |
| `trigger_price`, `trigger` | a trigger order's level and what it watches (`index_price`, `mark_price`, `last_price`) |
| `time_in_force` | `good_til_cancelled`, `good_til_day`, `fill_or_kill`, `immediate_or_cancel` |
| `post_only`, `reduce_only` | the order's flags |
| `creation_timestamp`, `last_update_timestamp` | milliseconds |

## `balances`: per-currency account summaries

A list, one record per currency (`private/get_account_summaries`, its
`summaries`), in that currency:

| Field | Is |
|---|---|
| `currency` | the currency (`BTC`, `ETH`, `USDC`, ...) |
| `balance` | the cash balance (deposits, premiums, settlements): no open PnL, no option value |
| `equity` | balance plus futures PnL plus the options' mark value |
| `margin_balance` | collateral counted against margin |
| `available_funds` | what is left to open positions with; 0 when the initial margin is used up |
| `available_withdrawal_funds` | what can be withdrawn |
| `initial_margin`, `maintenance_margin` | margin the positions and orders need |
| `total_pl` | PnL of the open positions since each opened |
| `delta_total` | the currency's delta: options' net delta plus futures in the coin |
| `options_delta`, `options_gamma`, `options_vega`, `options_theta`, `options_value` | the options' totals |
| `margin_model` | `segregated_sm`, `cross_sm`, `segregated_pm`, `cross_pm`: standard or portfolio margin, per currency or across |
| `total_equity_usd`, `total_margin_balance_usd` | cross-margined accounts only: the whole account in USD |

A field Deribit adds later appears here too: read the record rather than
assume these are all.
