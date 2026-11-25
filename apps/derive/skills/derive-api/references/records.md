# Derive's records, and how to read them

What `private/get_positions` and its neighbours send back, measured against
live mainnet. Each fact below came from a position-reading integration that
got it wrong first, and how it was established is kept beside it. A schema
check would have passed every one of these mistakes. Keep the evidence when you
build on a fact, so nobody "fixes" the arithmetic back out later.

## The three traps

### Greeks are per unit, in the long convention

A position's `delta`, `gamma`, `vega` and `theta` are for one unit of the
underlying held long. They are not scaled to the position and not signed by its
direction.

Established by ratio against `public/get_ticker`'s `option_pricing`, which is
per unit, for the same instrument, on a short position of 0.03:

| Greek | Position record | Ticker, per unit | Ratio |
|---|---|---|---|
| delta | 0.092954903 | 0.091980000 | 1.011 |
| theta | −1.881607192 | −1.862270000 | 1.010 |
| gamma | 0.001055946 | 0.001050530 | 1.005 |
| vega | 0.511199254 | 0.507120000 | 1.008 |

The ratios cluster at 1, not at the position size of 0.03, and the 0.5–1.1% gap
is the surface moving in the seconds between the two calls. So the record is
per unit. Passed through as it is, a short call reports positive delta and
negative theta, the long convention, with the sign inverted. A short option
collects decay and is short delta.

Multiply each greek by the signed `amount`. That corrects magnitude and sign
together:

| Greek | Raw | × −0.03 |
|---|---|---|
| delta | +0.0930 | −0.002789 |
| theta | −1.8816 | +0.056448 |
| gamma | +0.001056 | −0.0000317 |
| vega | +0.5112 | −0.015336 |

### `average_price` is a fee-adjusted basis

Reproduced to 1e-9 from its parts on a live position:

```
average_price = average_price_excl_fees + total_fees / amount
```

`amount` is negative on a short, so the fee term subtracts and can drive the
figure below zero. A short 0.03 ETH call sold at 6.1:

| Field | Value |
|---|---|
| `average_price_excl_fees` | 6.1, the premium traded |
| `total_fees` | 0.520026190349 |
| `amount` | −0.03 |
| `average_price` | −11.234206344966, which is not a price |

An option premium is never negative. The entry price is
`average_price_excl_fees`. Use `average_price` only when you want a
fee-inclusive basis, and never compare it with `mark_price`, which excludes
fees.

### `unrealized_pnl` is already funding-free

Derive keeps funding in `cumulative_funding` and `pending_funding`, so
`unrealized_pnl` is price only. Funding since the position opened is the sum of
those two. Paradex rolls funding into its PnL, and subtracting it here by the
same habit understates every perp position.

## The other fields

- `amount` is signed: negative is short. A position with `amount` 0 is closed;
  drop it.
- `liquidation_price` and `leverage` can be null. Keep null. A 0 reads as a
  real liquidation level at zero.
- `instrument_type` is `option`, `perp` or `erc20` (spot). Positions carry it.
  Orders do not, so read an order's kind from its name: a `PERP` segment, or an
  8-digit `YYYYMMDD` segment for an option, and anything else is spot.
- Balances come from `private/get_collaterals`: `currency` is the asset and
  `amount` the balance, negative when borrowed.
- Records sit under their own keys: `positions`, `orders` for both
  `get_open_orders` and `get_trigger_orders`, and `collaterals`.

## Instrument names

Derive's own grammar is at <https://docs.derive.xyz/trading/instrument-names.md>.

| Kind | Shape | Example |
|---|---|---|
| Spot | `<BASE>-<QUOTE>` | `ETH-USDC` |
| Perp | `<CURRENCY>-PERP` | `ETH-PERP` |
| Option | `<CURRENCY>-<YYYYMMDD>-<STRIKE>-<C\|P>` | `ETH-20261127-2400-C` |

- Names are uppercase and case-sensitive. The underlying is always the first
  segment.
- **A fractional strike writes its decimal point as `_`.** `BTC-20261225-102500_5-C`
  is struck at 102500.5. Read literally, the strike is wrong by orders of
  magnitude.
- A name may end in `-<RISK_UNIVERSE_ID>`, as in `ETH-PERP-42`. The suffix does
  not move the underlying, but read the universe from `risk_universe_id`, never
  from the name.
- Options settle at **08:00 UTC** on the named date by default (every ETH
  option listed on 6 Nov 2026 did). Some markets settle elsewhere: US-equity
  underlyings at 4pm New York time. The exact stamp is `option_details.expiry`
  on `public/get_instrument`. A date parsed from the name is right to the day
  and approximate to the hour.

## Anything that reads a whole account

A report, a data feed, a position tracker: anything that fetches every
position rather than answering one question.

- **Find subaccounts from both endpoints.** `node scripts/derive.mjs
  subaccounts` takes the union. Fall back to `_META.subaccount_id` only when
  both fail. A subaccount you cannot read should fail by itself and be
  reported, not be absent.
- **Sign every request,** public ones included, as the client does.
- **Detect a refused credential by error code, not HTTP status.** Every one of
  them is HTTP 200. Report it as a refusal, never as an empty account.
- **Fetch every dataset for every subaccount,** in parallel, and tag each
  record with its `subaccount_id` so rows from several subaccounts stay
  attributable.
- **Merge resting and trigger orders only when both calls succeeded.** A merge
  of one half makes the missing orders look cancelled. If either failed, report
  neither and keep what you had.
- **Drop zero-amount positions.**
- **Keep numbers as the decimal strings Derive sends,** and do arithmetic in
  scaled integers or decimals. Floats bring back artefacts like
  `3.4999999999999996`.
- **Null means the venue does not give it,** never 0.

| Field | From | Note |
|---|---|---|
| symbol | `instrument_name` | as sent |
| kind | `instrument_type` | `erc20` is spot |
| size | `amount` | signed; negative is short |
| entry price | `average_price_excl_fees` | not `average_price` |
| unrealised PnL | `unrealized_pnl` | already funding-free; subtract nothing |
| funding PnL | `cumulative_funding` + `pending_funding` | |
| delta, gamma, vega, theta | the greek × `amount` | per unit in the record |
| strike | parsed from the name | `_` is the decimal point |
| expiry | `option_details.expiry`, or parsed from the name | 08:00 UTC by default |
| liquidation price | `liquidation_price` | null stays null |

## How to check a mapping instead of trusting it

These checks do not depend on a field meaning what its name says, which is why
they caught what a schema check could not:

- **Greeks:** compare the position's greek with `option_pricing` from
  `public/get_ticker` for the same instrument. A ratio near 1 means per unit; a
  ratio near the position size means already scaled.
- **Economic sanity:** an option premium cannot be negative, and a short
  option cannot have positive delta or negative theta. A value that breaks one
  of these is a mapping bug, whatever the field is called.
- **Derived fields:** rebuild them from their parts. `average_price` was pinned
  down by reproducing it from three other fields.
- **Coverage:** cross-check enumeration against an independent endpoint. The
  missing subaccount surfaced only because `private/get_all_portfolios` showed
  value where `private/get_subaccounts` had listed nothing to read.

Test a mapper offline on recorded records: a short option, a fractional
strike, a null liquidation price, an order with no `instrument_type`. Assert on
signs (delta below 0 on a short call), because those catch exactly what schema
validation misses.
