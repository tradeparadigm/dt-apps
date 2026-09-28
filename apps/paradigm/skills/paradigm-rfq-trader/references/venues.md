# Venues — per-venue cookbook for paradigm-rfq-trader

The skill body is **venue-agnostic**. This file is the per-venue
recipe: instrument-name format, fair-value sources, settlement
verification, and venue-specific quirks. Adding a new DRFQv2 venue
means appending a section here in the same shape — the SKILL.md
workflow stays the same.

Currently in scope: **PRDX (Paradex, primary)** and **DBT (Deribit)**.

Each venue section answers four questions, in this order:

1. **Naming** — how venue-native instrument names look (for the
   `GET /v2/drfq/instruments/` lookup).
2. **Fair value** — which tool(s) to call to benchmark a quote or
   ranking, by `instrument.kind`.
3. **Edge syntax** — what "+X over mark" means on this venue.
4. **Settlement check** — how to verify the cleared trade landed.

Plus any venue-specific quirks at the end.

---

## PRDX — Paradex (primary focus)

### Naming

| Product | Format | Example |
|---|---|---|
| Perpetual | `<BASE>-USD-PERP` | `BTC-USD-PERP`, `ETH-USD-PERP` |
| Dated future | `<BASE>-USD-<DDMMMYY>` | `BTC-USD-27JUN26` |
| Option | `<BASE>-USD-<DDMMMYY>-<STRIKE>-<C\|P>` | `BTC-USD-8MAY26-90000-C` |

Day **not** zero-padded. Month uppercase 3-letter. `-USD-` infix is
the Paradex distinguisher vs Deribit.

### Counterparties / LP coverage

Default to every LP eligible for PRDX, named explicitly. Paradigm has no open
broadcast on this path: an empty `counterparties` list is a 400.

1. Call `GET /v2/drfq/counterparties/?venues=PRDX&group=LP` and **page through
   the entire result**. The answer is `{count, next, results}`. `next` is a
   bare cursor token rather than a URL, so page two is
   `?venues=PRDX&group=LP&cursor=<token>`. Carry the filters on every page: the
   cursor holds an offset only, so a bare `?cursor=` refilters against every
   desk and returns page one. There is no `has_more`. Stopping at page 1
   silently drops LPs, which is what "not all LPs got the RFQ" means.
2. Keep the desks whose `groups` carry `LP` and whose `venues` carry `PRDX`.
   Those two lists are what the endpoint returns per desk. Pass their
   `desk_name` values as `counterparties` to `POST /v2/drfq/rfqs/` and surface
   the count (`all N PRDX LPs`). The desk name IS the ticker; nothing returns
   a key called `ticker`.

Narrow to a directed subset only when the user names specific desks.

When the lookup fails or comes back empty, stop and ask which desks to send
to. An empty list does not broadcast, it errors.

### Fair value

Paradex exposes a public REST API, so this needs no credential.
Base: `https://api.prod.paradex.trade/v1`. Pull fair value with
`web_fetch`:

**`kind = FUTURE` (perp / dated future):**

- `web_fetch .../bbo/<market>` → best bid/ask.
- `web_fetch .../markets/summary?market=<market>` → mark + funding +
  24h stats.
- `web_fetch .../orderbook/<market>` → walk the book for the full RFQ
  size. This is the implicit "what would I get on-screen?" benchmark
  that every RFQ price should be compared against.

**`kind = OPTION`:**

- `web_fetch .../markets/summary?market=<market>` per leg. Read `mark_price`,
  `mark_iv` and `delta` at the top level, and `vega` from the nested `greeks`
  object, which also carries `delta` and `gamma`.
- **`mark_iv` is a decimal here**: `0.52153206` means 52.15%. Deribit returns
  the same quantity as `47.74`. Read the unit before you do arithmetic on it.
- Pull `<BASE>-USD-PERP` mark for the underlying spot.
- Aggregate for multi-leg: `structure_mark = Σ (ratio × leg_mark ×
  side_sign)`, net delta = Σ (ratio × δ × side_sign), net vega
  similar.
- BS / IV math itself: use standard Black-Scholes greek formulas.

### Edge syntax

- "Y bps over mid" → `price = mid × (1 + Y/10000)` (ask) or
  `× (1 - Y/10000)` (bid). Mid = `(best_bid + best_ask) / 2` from
  the public `.../bbo/<market>` endpoint.
- "Tighten the BBO by Z bps" → quote inside the current Paradex
  best. Flag if it implies a negative spread.
- "X vol over mark IV" (options only) → bump per-leg IV by X **vol points**,
  which is `X / 100` on Paradex's decimal `mark_iv`. Adding 5 to `0.52` quotes
  552 vol. Reprice via BS and re-aggregate.

### Settlement check

No Paradex account integration at this skill version. After the cross:

- Surface `trade_id` from `GET /v2/drfq/trades/`.
- Tell the user the block will appear on their Paradex account and
  to verify there directly.

### Quirks

- Same strike can exist as INVERSE *and* LINEAR margin variants —
  filter on `margin_kind` when resolving by name to disambiguate.

---

## DBT — Deribit

### Naming

| Product | Format | Example |
|---|---|---|
| Option | `<BASE>-<DDMMMYY>-<STRIKE>-<C\|P>` | `BTC-8MAY26-90000-C`, `ETH-10MAY26-2375-P` |
| Future | `<BASE>-<DDMMMYY>` | `BTC-27JUN26` |
| Perpetual | `<BASE>-PERPETUAL` | `BTC-PERPETUAL` |

Day **not** zero-padded (same convention as Paradex). No `-USD-`
infix.

### Fair value

**`kind = OPTION` (the dominant Deribit RFQ product):**

- `web_fetch`
  `https://www.deribit.com/api/v2/public/ticker?instrument_name=...` per leg.
  Returns `mark_price`, `best_bid_price`, `best_ask_price`, `mark_iv`,
  `bid_iv`, `ask_iv`, `open_interest`, and the greeks NESTED under `greeks`
  (`delta`, `gamma`, `vega`, `theta`, `rho`). There is no top-level `mark`,
  `bid`, `ask` or `delta`. Public, so it needs no credential.
- A `deribit__get_ticker` tool returns the same payload. Use it when the host
  has one.
- Pull `BTC-PERPETUAL` / `ETH-PERPETUAL` mark for underlying spot.
- Aggregate exactly like the PRDX option case.

**`kind = FUTURE` (perp / dated future):**

- The same public ticker endpoint for the instrument. Returns mark and BBO.
- Cross-venue check vs Paradex via the public `.../bbo/<market>`
  endpoint is optional; Deribit's own book is the relevant benchmark
  since the trade settles there.

### Edge syntax

- "Y bps over mark" → `price = mark × (1 ± Y/10000)`. "Mark" here
  is the ticker's `mark_price` (in BTC for inverse options).
- "X vol over mark IV" → bump per-leg IV by X, added straight to Deribit's
  percentage `mark_iv` (e.g. `47.74`), reprice via BS,
  re-aggregate.
- "Tighten the BBO" → quote inside Deribit's current best bid/ask.

### Settlement check

This skill reads no Deribit account. After the cross:

- Surface `trade_id` from `GET /v2/drfq/trades/`.
- Tell the user the block will appear on their Deribit account and
  to verify there directly.

### Quirks

- Deribit option prices are in **BTC/ETH terms** for inverse
  options (the common case), not USD. When surfacing dollar
  notional, multiply by the underlying mark.
- `mark_iv` is in **percentage** form here (`47.74` = 47.74%). Paradex returns
  the same quantity as a decimal (`0.4774`), so a vol bump is a straight
  addition on Deribit and a division by 100 first on Paradex.
- For perps/futures on Deribit, prices ARE in USD.

---

## Adding a new venue (future scope)

To extend the skill to `BYB` (Bybit), `BIT` (Bit.com), or any
future DRFQv2 venue:

1. Append a section to this file in the same four-part shape.
2. If the venue needs a new fair-value source, list
   it under "Compatibility" in `SKILL.md`.
3. No changes to the SKILL.md workflow body — Step 2/3a/3b/4 all
   delegate to this file.

Strategy codes and product kinds (`OPTION` / `FUTURE` / `LOAN` /
`SPOT`) are venue-independent — see
[`instruments.md`](instruments.md) for the full strategy-code table.
