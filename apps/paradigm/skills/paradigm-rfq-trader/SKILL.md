---
name: paradigm-rfq-trader
description: >
  Trigger institutional block trades via Paradigm's DRFQv2 flow. The
  workflow is venue-agnostic — resolve instruments, build the RFQ /
  order payload, benchmark, run a confirmation gate, submit, verify
  settlement. Per-venue specifics (fair-value sources, naming
  conventions, edge syntax, settlement checks) live in
  references/venues.md. In scope today: PRDX (Paradex, primary focus)
  and DBT (Deribit). Adding more DRFQv2 venues is a references/venues.md
  edit, not a skill-body change. Covers takers (build, benchmark,
  cross) and makers (poll, price, manage). Every state-changing action
  goes through an explicit confirmation gate. Use when the user asks
  to "send a Paradigm block RFQ", "block-trade X BTC", "send a BTC
  straddle on Paradex / Deribit", "quote rfq_X", "hit the best bid",
  "cancel rfq_X". Does NOT cover small central-order-book trades,
  post-trade analysis (paradigm-block-analyst),
  historical tape (paradigm-data-discovery).
compatibility: >
  Calls the DRFQv2 REST API through the credential proxy. Read the
  paradigm-api skill first and use its signing helper: it owns the three
  headers, the signed string and the two placeholders, and this skill assumes
  them. Needs both Paradigm credentials enrolled. Per-venue fair-value
  dependencies are in references/venues.md: the Paradex public REST API via
  web_fetch for PRDX RFQs, and Deribit's public ticker over web_fetch for DBT.
metadata:
  author: tradeparadigm
  version: "1.0"
---

# Paradigm RFQ Trader

Drives the Paradigm DRFQv2 lifecycle, taker and maker, over REST through the
credential proxy.

This file covers the workflow and the confirmation gate. The `paradigm-api`
skill covers transport, auth and signing, and `references/venues.md` covers
everything that varies between settlement venues.

## Scope

| Venue | Status |
|---|---|
| `PRDX` (Paradex) | **Primary focus.** Perp, dated future, option |
| `DBT` (Deribit) | Supported. Option is the dominant product; perp/future also supported |
| `BYB` (Bybit), `BIT` (Bit.com) | Out of scope at this version. Add by appending to `references/venues.md` |

See [`references/venues.md`](references/venues.md) for the per-venue
recipe (naming, fair-value tools, edge syntax, settlement check).

**Out of scope at this skill version:**

- Small / liquid orders on a venue's central order book.
- Post-trade analysis of a filled block → `paradigm-block-analyst`.
- Historical tape queries → `paradigm-data-discovery`.
- Heavy options pricing math (greek formulas, IV surface fitting)
  → use standard Black-Scholes greek formulas. The math is the
  same regardless of settlement venue.

## Trigger

Fire on live RFQ-lifecycle intent. Examples:

- *"send a block RFQ for 500 BTC perp"*
- *"send a BTC 8MAY26 90/80 risk reversal on Paradex"*
- *"Deribit BTC strangle, 100 contracts, send the RFQ"*
- *"quote rfq_12345 at 2 bps over mid"*
- *"quote rfq_X at +0.5 vol over mark IV"*
- *"hit the best bid on this RFQ"*
- *"cancel rfq_12345"*

If the user doesn't specify a venue, ask — don't guess. The choice
(PRDX vs DBT) determines counterparties, settlement, and fees.

Do **not** fire on:

- Direct central-order-book trades on a venue.
- Post-trade analysis of a filled block JSON → `paradigm-block-analyst`.
- Historical tape queries → `paradigm-data-discovery`.
- RFQs on Bybit / Bit.com — currently out of scope.

## Endpoints

Every path below sits under `/v2/drfq/`, which is the only surface the two
Paradigm credentials are scoped to. A request outside it gets no credential and
Paradigm answers `403 Invalid API Access Key`.

| Call | Method and path | Confirmation? |
|---|---|---|
| Signing self-test | `GET /v2/drfq/echo/` | no |
| Body-byte self-test | `POST /v2/drfq/echo/` with any body | no |
| Resolve a venue-native name to an `instrument_id` | `GET /v2/drfq/instruments/` | no |
| One instrument | `GET /v2/drfq/instruments/{id}/` | no |
| Maker desks and their venue eligibility | `GET /v2/drfq/counterparties/` | no |
| List RFQs, filter by `role`, `state`, `venue`, `strategies` | `GET /v2/drfq/rfqs/` | no |
| Create an RFQ | `POST /v2/drfq/rfqs/` | **yes** |
| RFQ snapshot | `GET /v2/drfq/rfqs/{id}/`, `/bbo/`, `/orders/` | no |
| List orders | `GET /v2/drfq/orders/` | no |
| Quote or cross | `POST /v2/drfq/orders/` | **yes** |
| Amend an order | `PUT /v2/drfq/orders/{id}/` | **yes** |
| Cancel one RFQ or order | `DELETE /v2/drfq/rfqs/{id}/`, `DELETE /v2/drfq/orders/{id}/` | no |
| Cancel a batch | `DELETE /v2/drfq/orders/?rfq_id=<id>&state=<state>` | **yes, unless every filter resolved** |
| Your cleared blocks | `GET /v2/drfq/trades/`, `GET /v2/drfq/trades/{id}/` | no |
| The public block tape | `GET /v2/drfq/trade_tape/` | no |
| Price a multi-leg structure | `POST /v2/drfq/pricing/` | no |
| Maker circuit-breaker status | `GET /v2/drfq/mmp/status/` | no |
| Reset the circuit-breaker | `PATCH /v2/drfq/mmp/status/` body `{"rate_limit_hit": false}` | **yes** |
| Platform state | `GET /v2/drfq/platform_state/` | no |
| Cancel every DRFQ order | `DELETE /v2/drfq/orders/` | **yes, destructive** |

Two things this skill does NOT do, because they live outside `/v2/drfq/`:

- A desk overview across products. Positions, identity and the order-book and
  forward-swap venues sit under `/v1/`, and one of them is on another host.
  `GET /v2/drfq/platform_state/` and `GET /v2/drfq/mmp/status/` are the DRFQ
  parts of it.
- A kill switch across products. `DELETE /v2/drfq/orders/` cancels every DRFQ
  order. It leaves your own open RFQs alone, and does not touch order-book
  quotes or forward-swap orders. Say all of that when you use it, because a
  user asking to cancel everything means everything.

## What you have to do yourself

Nothing sits between you and Paradigm now, so these are yours.

**Never send a batch cancel with an unresolved filter.** You drop a parameter
that has no value, so an unresolved `rfq_id` turns a batch cancel into the
cancel-all. Fill every filter in before you send it, and gate the call when you
cannot.

**Encode the query exactly once, and sign what you send.** The signed path
carries the query string, so build it once and use the same bytes for both.
Write a bool as `true` or `false`, repeat the key for a list, and drop a
parameter that has no value. The helper takes the whole target, query included.

**Serialise the body with no spaces** and sign those bytes. Post the same
bytes. `JSON.stringify` gives you this; re-serialising after signing does not.

**Walk the counterparty pages.** `GET /v2/drfq/counterparties/` is paginated.
Page one is not the desk list. Follow the cursor to the end before you use it,
and stop at 100 pages.

**An RFQ body needs six fields and Paradigm refuses it without them.**
`venue`, `legs`, `quantity`, `account_name`, `counterparties` and
`is_taker_anonymous`. `state` has a server default of `OPEN`, and `label` is
the only other optional one. Write enum values bare: `OPEN`, not
`RFQState.OPEN`, and `MAKER`, not `AuctionRole.MAKER`.

**`counterparties` can never be empty.** Paradigm answers `At least one
counterparty must be specified`. There is no open-broadcast fallback on this
path, so a failed counterparty lookup means you stop and say so rather than
sending an empty list.

**A maker quote needs `account_name`; a taker cross does not.** Paradigm
answers `This field is required` on a maker order without it. A taker crossing
an existing order inherits the credential from the RFQ.

**Anonymous needs three LPs.** With `is_taker_anonymous: true` and fewer than
three counterparties in the `LP` group, Paradigm answers `To send an Anonymous
RFQ, please select at least 3x LPs`. Send `false`, or add LPs, and say which
you did.

**Keep the status code and the request id.** Paradigm returns an
`x-request-id` header. Quote it with the status code and the body when you
report a failure, because that is what support can trace.

**Ask which desk on a multi-desk key.** One key can cover several desks, and a
`Paradigm-Account` header picks one. Nothing is enrolled for it, so you write
the header. Ask the user before the first state-changing call, and put the desk
in the confirmation gate. Leave the header off and Paradigm picks, which is a
trade on a desk nobody chose.

## Setup

Both credentials are enrolled through the app's credentials form, and the proxy
substitutes them. You never see a key and never ask for one.

If a user asks what their key is, say it lives in the credential store and that
the proxy puts it on the request. If a call comes back `403 Invalid API Access
Key`, the placeholder reached Paradigm unsubstituted, which usually means the
path fell outside `/v2/drfq/`.

## Roles

| Role | Steps |
|---|---|
| **Taker** — sources liquidity | 1, 2, 3a, 4 |
| **Maker** — provides liquidity | 1, 2, 3b, 4 |

DRFQv2 has no separate quote object. Maker quoting and taker crossing
both POST to `/v2/drfq/orders/`. Only `side` and
`time_in_force` differ (GTC for maker, FOK for taker cross).

## Step 1 — Gather inputs

Identify role and venue from the user's phrasing. If venue is
ambiguous, ask.

**Taker:**

| Field | Meaning |
|---|---|
| `venue` | `PRDX` or `DBT` (see scope table; ask if unspecified) |
| `legs` | `{instrument_id, ratio, side, price?}` rows. Outright = 1 leg; spread / straddle / RR = 2 legs; condors etc. = more. `side` defines structure orientation — see **Direction** below |
| `quantity` | Decimal string in base units |
| `counterparties` | Desk tickers, and **never empty**. Default to every LP eligible for the venue: `GET /v2/drfq/counterparties/?venues=<venue>&group=LP`, paged to the end, then take the desks whose `groups` carry `LP` and whose `venues` carry this venue. Narrow to named desks only when the user names them. A failed lookup stops the RFQ, because an empty list is a 400 (see Step 3a · 1) |
| `is_taker_anonymous` | Hide the taker desk from makers. **Required.** `true` needs at least three `LP` counterparties |
| `state` | `OPEN` sends it now, `DRAFT` stages it. Optional, and the server defaults it to `OPEN` |
| `account_name` | The account to bill. **Required**, and a missing one is a 400 |
| `label` | Idempotency tag, echoed back. Optional |

**Maker:**

| Field | Meaning |
|---|---|
| `rfq_id` | RFQ to quote — fetch it first to learn `venue` + `kind` |
| `side` | `BUY` (bid) / `SELL` (offer). Two-way = two `POST /v2/drfq/orders/` calls |
| `price` or `edge` | Absolute price, or an edge spec interpreted per `references/venues.md` for that RFQ's venue |
| `quantity` | Defaults to RFQ quantity |
| `type` | `LIMIT` (default) or `HIDDEN` |
| `time_in_force` | `GOOD_TILL_CANCELED` (rest) or `FILL_OR_KILL` (cross) |

### Direction — read before building `legs`

Leg `side` values define the *structure*; the package you submit defines
the *direction you hold it*. To go **long** a structure, configure the
leg sides so the package IS the position the user wants and submit it as
a **BUY** (positive quantity). Do **not** also flip every leg to a
"short" orientation and then SELL — that double-negates back to long
(the common bug).

Use **SELL on the package only** when you built a *conventional /
textbook* structure and the user wants its inverse — e.g. "short call
spread" = build the conventional debit call spread (BUY lower call +
SELL higher call), then SELL the package.

- Bullish call spread → BUY lower-strike call + SELL higher-strike call,
  submit **BUY**. "Short call spread" → same legs, submit **SELL**.
- Bearish put spread → BUY higher-strike put + SELL lower-strike put,
  submit **BUY**.
- Bullish risk reversal (e.g. 90/80) → BUY 90 call + SELL 80 put, submit
  **BUY**. Bearish → SELL call + BUY put in the legs, submit **BUY**.
- **Outright** (1 leg) → no structure to orient: short = a single leg
  `side=SELL`; don't also flip a package direction.

**Worked example — "short a 90000/95000 call spread"** (the textbook case
the bug bites): the *conventional* structure is the debit call spread, so
build it conventionally and short the **package**, never the legs.

- legs: `BUY 90000-C` (lower strike) **+** `SELL 95000-C` (higher strike)
- package: submit **SELL** to be short it.
- Do **not** invert to `SELL 90000-C + BUY 95000-C` *and* submit SELL — that
  double-negates back to long the call spread. The lower strike is always
  the BUY leg in the conventional build.

The cross `side` at `POST /v2/drfq/orders/` (Step 3a · 5) is a separate
matching-mechanics concern — see there.

If anything is ambiguous, ask before calling tools.

## Step 2 — Resolve instrument IDs

Paradigm references legs by integer `instrument_id`. For each leg:

```
GET /v2/drfq/instruments/?venue=<venue>&venue_instrument_name=<name>
```

Capture `results[0].id` and `results[0].kind`. The `kind` (`OPTION`
vs `FUTURE`) drives the fair-value approach in Step 3.

For venue-native instrument naming, see
[`references/venues.md`](references/venues.md). Cache id + kind
for the session; do not invent IDs.

## Step 3a — Taker flow

1. **Resolve counterparties, then create the RFQ.** Unless the user named
   specific desks, default to every LP eligible for the venue:
   - Call `GET /v2/drfq/counterparties/?venues=<venue>&group=LP` and **page
     through every result**. Follow the cursor, `next` or `has_more` to the
     end. A partial list silently drops LPs.
   - Keep the desks whose `groups` carry `LP` and whose `venues` carry this
     venue, and pass their tickers as `counterparties`. Capture the count `N`.
   - **A failed or empty lookup stops here.** Paradigm refuses an empty
     `counterparties` list, so say the lookup failed and ask which desks to
     send to. Do not send the RFQ.
   - With `is_taker_anonymous: true`, Paradigm needs at least three LPs. With
     fewer, either send `false` or add desks, and say which you did.

   Then `POST /v2/drfq/rfqs/` with all six required fields. Capture `rfq_id`.
   Show: id, venue, legs, quantity, counterparties (`all N PRDX LPs` or the
   named desks), expiry.
2. **Stream quotes live** — every 1 to 3 s poll all three of
   `GET /v2/drfq/rfqs/{rfq_id}/`, `GET /v2/drfq/rfqs/{rfq_id}/bbo/` and
   `GET /v2/drfq/rfqs/{rfq_id}/orders/`. There is no composite call, so one
   request gives you the RFQ and no book. Then **surface
   each new or improved quote the instant it appears** — do **not** wait
   for the auction to close before showing anything. Keep one compact
   live ladder that updates in place: best price on top (ties → earlier
   timestamp), each row `desk · side · price · size · age · offset vs
   fair`. Mark the current best. **On every tick, check the RFQ `state`
   / `closed_reason` first:** if the RFQ has left `OPEN` for a non-fill
   reason (`EXPIRED`, `EXECUTION_LIMIT`, rejected / errored), **stop the
   quote loop and surface the failure** per Step 3a · 7 — never keep
   spinning on a dead RFQ. Otherwise repeat until the user crosses,
   cancels, or the RFQ expires.
3. **Benchmark inline** — fold the venue's fair-value reference (per
   `references/venues.md`, by `venue` + `kind`) into the ladder's
   `offset` column: `price − fair` in the venue's natural units (bps for
   linear; absolute + implied-vol bump for options). Pull it once, refresh
   on a slower cadence than the quote poll.
4. **Confirmation gate** (see below). Wait for explicit `yes`.
5. **Cross** — `POST /v2/drfq/orders/` with `rfq_id`, `side`,
   `"type": "LIMIT"`, `"time_in_force": "FILL_OR_KILL"`, `price`, `quantity`
   and `legs`. `side` is opposite the resting order being taken.
   This cross `side` is matching mechanics (lift an offer = BUY, hit a
   bid = SELL) and is independent of the structure's long/short
   orientation, which the leg sides already fixed at create-time (see
   Direction). Response is async-first (`state: OrderState.PENDING`) — poll
   `GET /v2/drfq/orders/` and branch on the terminal state:
   - **`CLOSED`** → fetch `trade_id` from `GET /v2/drfq/trades/` and match on the RFQ,
     then follow the venue's settlement-check recipe in `references/venues.md`.
     A FOK cross that fills nothing also terminates — treat a closed order
     with no resulting trade as a non-fill, not an open wait.
   - **`REJECTED` / failed (order terminal, or BlockTrade `state=REJECTED`)**
     → **stop. Do not poll `trades` for a fill that will never come.**
     Surface the rejection with full detail per Step 3a · 7.
6. **Cancel** — on abort, `DELETE /v2/drfq/rfqs/{rfq_id}/`.
7. **Errors & rejections** — the single rule for failure handling. On
   **any** RFQ / order / trade terminal failure (non-fill RFQ
   `closed_reason`, rejected / failed order state, BlockTrade
   `state=REJECTED`), **halt fill-polling immediately** — the common bug
   is continuing to wait for fills on an RFQ that is already dead.
   Gather the maximum error detail the payloads expose and quote it
   verbatim: RFQ `closed_reason`, the order's terminal `state`,
   `BlockTrade.state`, plus any `error` / `reason` / `message` / `code` /
   request-id / timestamp fields present. Present it plainly — what
   failed, why (raw reason / code), and the next step (re-send, widen
   counterparties, adjust price) — rather than a silent spinning loop.
   Polling is the only mechanism here, so this branch is what surfaces a
   rejection.

## Step 3b — Maker flow

1. **Find open RFQs** — poll
   `GET /v2/drfq/rfqs/?state=OPEN&role=MAKER`
   every 1–3 s. Filter by `venue` if the user only wants certain
   venues.
2. **Fair value** — follow the venue's fair-value recipe in
   `references/venues.md`.
3. **Optional pricing helper** — for multi-leg structures, call
   `POST /v2/drfq/pricing/` with `bid_price`, `ask_price` and `legs`
   to split a structure price across legs the way Paradigm will.
4. **Apply edge** — the edge syntax depends on the venue; see
   `references/venues.md`. Common shapes:
   - Linear: "Y bps over mid", "tighten the BBO by Z".
   - Option: "X vol over mark IV", "Y bps over option mark",
     absolute price.
   Show the implied edge before going to the gate.
5. **Confirmation gate**. Wait for explicit `yes`.
6. **Post** — `POST /v2/drfq/orders/` with `rfq_id`, `side`, `account_name`,
   `"type": "LIMIT"`, `"time_in_force": "GOOD_TILL_CANCELED"`, `price`,
   `quantity` and `legs`. A maker order without `account_name` is a 400.
   Two-way = two calls.
7. **Manage lifecycle** — poll each 1–3 s:
   - `GET /v2/drfq/orders/?rfq_id=...` — surface when no longer
     top-of-book.
   - `GET /v2/drfq/trades/`, matched on the RFQ — surface fills.
   - `GET /v2/drfq/mmp/status/` — circuit-breaker status. If
     `rate_limit_hit: true`, all desk orders are paused; reset to
     re-arm (gated).
   Amend by cancel + new post; same confirmation gate.

## Step 4 — Confirmation gate

**Always** present this block and wait for explicit `yes` before any
state-changing call: `POST /v2/drfq/rfqs/`, `POST /v2/drfq/orders/`,
`PUT /v2/drfq/orders/{id}/`, `DELETE /v2/drfq/orders/` with no id, and
`PATCH /v2/drfq/mmp/status/`.

**Open the block with mainnet or testnet, and say which credential you read it
from.** The credential decides the host, and the machine you run on says
nothing about it. A box named testnet routinely holds a mainnet key, so a
wrong read here is a real trade the user believed was paper. The `paradigm-api`
skill covers how to tell them apart.

The block has two parts: (1) the **assembled call** — the exact method, path
and body that will run on `yes`, fully resolved (integer `instrument_id`s,
leg sides, `quantity`, `counterparties`, `venue`, `time_in_force`, and the
desk when the key covers more than one); and (2) a one-line **fair-value**
reference. Showing the assembled call is what
"live-money confirmation" means — the user sees precisely what will be
submitted. Assemble it *now*, before the gate; do not defer assembly to
after `yes`.

Every example below opens with the environment for that reason.

Canonical taker example (PRDX perp; same structure for any venue —
swap in the venue's fair-value section per `references/venues.md`):

```
CONFIRM RFQ — MAINNET (CRED_PARADIGM_MAINNET_ACCESS) — taker
BTC-USD-PERP (id 98765) · PRDX
Will call on yes:
  POST /v2/drfq/rfqs/
  {"venue": "PRDX",
   "legs": [{"instrument_id": 98765, "ratio": 1, "side": "BUY"}],
   "quantity": "500",
   "account_name": "desk-main",
   "counterparties": [...14 LP tickers],   # resolved and paged
   "is_taker_anonymous": true,             # 14 LPs, so the 3 LP minimum holds
   "label": "..."}
BUY 500 BTC → all 14 PRDX LPs                 ~$48.23M
Fair: mid $96,455 · BBO 96,450/96,460 (10 bps) · walk 500 ~$96,612 (+16 bps)
[yes / no / adjust]
```

Keep it tight — environment line, header line, the assembled `Will call on yes:` block, action
line + notional, one fair-value line, prompt. Don't restate fields the user
already gave. For multi-leg structures, the action line states the **net**
direction the taker will hold (long / short the structure), confirmed against
**Direction** (Step 1) — not merely a restatement of leg sides — while the
assembled call shows the literal per-leg `side`s.

For options or for Deribit, the **structure of the block is the
same** — environment line, header line, assembled call, leg(s) listed, fair-value reference,
sizing line — but the fair-value section is shaped per
`references/venues.md` for that venue + kind. Options **must** show, *inside
the confirmation block itself* (not only in an earlier step), a per-leg line
with `mark + mark_iv + delta + vega`, the **underlying spot** (pull
`BTC-USD-PERP` mark on PRDX / `BTC-PERPETUAL` on DBT), and an aggregated
structure mark + net delta/vega. Deribit options show prices in BTC terms
(not USD). Example (PRDX risk reversal):

```
CONFIRM RFQ — MAINNET (CRED_PARADIGM_MAINNET_ACCESS) — taker
BTC 8MAY26 90/80 risk reversal · PRDX · short/bearish
Will call on yes:
  POST /v2/drfq/rfqs/
  {"venue": "PRDX",
   "quantity": "100",
   "legs": [{"instrument_id": 50121, "ratio": 1, "side": "SELL"},    # 90000-C
            {"instrument_id": 50144, "ratio": 1, "side": "BUY"}],    # 80000-P
   "account_name": "desk-main",
   "counterparties": ["LP1", "LP2"],
   "is_taker_anonymous": false,            # two LPs, under the 3 LP minimum
   "label": "..."}
  90000-C  mark 0.021 · IV 58% · Δ +0.34 · vega 9.2
  80000-P  mark 0.018 · IV 61% · Δ −0.22 · vega 8.1
  Underlying BTC-USD-PERP mark $96,455 · net structure mark 0.003 · net Δ +0.12
[yes / no / adjust]
```

**Responses:** `yes` → call the tool. `no` → abort. `adjust <field>
<value>` → re-render. Common adjust verbs:

- Linear: `adjust price`, `adjust quantity`, `adjust edge (bps)`.
- Option: `adjust quantity`, `adjust edge (vol)`,
  `adjust counterparties`.

Re-pull the venue's fair-value reference before re-rendering.

Never submit without explicit confirmation — even if the user
pre-states "just send it" in the same message.

## Post-trade handoff

- **Post-fill analysis** — pass the trade JSON to
  `paradigm-block-analyst` for fill-quality benchmarking.
- **Settlement verification** — venue-specific. See the "Settlement
  check" subsection per venue in `references/venues.md`.
- **Historical context** — `paradigm-data-discovery` over the S3
  tape.
- **Hedging the new exposure** — place delta hedges directly on the
  venue's central order book.

## Output format

**Terse by default.** A few tight lines and one table beat a wall of
prose. Surface only what the trader acts on:

- One header line (instrument · side · quantity), or a small legs table
  for multi-leg.
- The **live quote ladder** during an open RFQ (Step 3a · 2), updated in
  place as quotes stream in — not re-printed in full each tick.
- One fair-value line, shaped per the venue's recipe in
  `references/venues.md`.
- The slim confirmation block before any state-changing call (Step 4).
- A one-line result on success (`rfq_id` / `order_id` / `trade_id`).
- **Data trace** — one line, the concrete tools actually called, e.g.
  `instruments → rfq, bbo, orders → create rfq`.

Drop empty sections. Don't restate inputs the user just gave. Never
invent fair-value numbers when a data source is unreachable — say so.

## Caveats

- **Live-money venue.** Never auto-execute. The confirmation gate
  is non-negotiable.
- **Credentials live in the credential store, not in chat.** Refuse
  to echo a key or ask the user to paste one. The proxy holds both.
- **Async-first orders.** A posted order comes back `PENDING`. Poll
  `GET /v2/drfq/orders/` for terminal state, and branch on failure,
  not just on `CLOSED`. A rejected / failed RFQ or order must stop the
  fill-poll and surface the error (Step 3a · 7), never hang waiting.
- **Polling, because there is no stream here.** Paradigm's WebSocket takes the
  access key as an `api-key` query parameter, and the proxy substitutes into a
  request it signs, not a socket URL. Drive the quote ladder by polling every
  1 to 3 seconds and read a rejection off the polled terminal state.
- **Venue scope:** PRDX (primary) + DBT today. Adding a venue is a
  `references/venues.md` edit, not a skill-body change. Bybit,
  Bit.com, and any future DRFQv2 venue plug in the same way.
- **The OpenAPI spec is the endpoint reference.** This skill lists the paths it
  uses and does not duplicate payload shapes.
- Not financial advice. Fair-value benchmarks are reference, not a
  recommendation.

## References

- [`references/venues.md`](references/venues.md) — **per-venue
  cookbook**: naming, fair-value tools, edge syntax, settlement
  check. The first place to look when extending the skill.
- [`references/instruments.md`](references/instruments.md) —
  venue-independent enum semantics (kinds, margin kinds, strategy
  codes / `StrategyCodeEnum`).
- The `paradigm-api` skill — the three headers, the signed string, the two
  placeholders and the cached signing helper. `GET /v2/drfq/echo/` is the
  end-to-end signing self-test.

For payload shapes and enums, read the OpenAPI spec at
[`tradeparadigm/mono#34164`](https://github.com/tradeparadigm/mono/pull/34164)
directly.
