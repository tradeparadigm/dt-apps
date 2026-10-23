---
name: paradigm-api
description: >
  Paradigm's DRFQv2 RFQ API: recent block trades on the public block tape
  (`paradigm__paradigm_drfqv2_trades` with `mode: "tape"`), verifying the
  credentials, instruments, counterparties, listing and creating RFQs, quotes
  and crosses, cancels, your trades, leg pricing and the maker
  circuit-breaker. The tools are `paradigm__*` MCP tools, called by exact name,
  and need the user's Paradigm credentials enrolled. Use for ANY request to
  Paradigm's REST API, including "recent block trades", "what would this
  spread price at" (pricing legs without sending an RFQ), "is my Paradigm key
  working", "list my Paradigm RFQs" or "cancel rfq_...". The tape here is the
  latest prints only: volumes over time, comparisons across days and whether a
  structure traded before are paradigm-data-discovery's, and so is the block
  tape without credentials. The RFQ workflow and its
  confirmation gate are paradigm-rfq-trader.
metadata:
  author: tradeparadigm
---

# Paradigm

Every Paradigm REST call goes through the `paradigm` MCP server. The server
signs for you. Do not look for credentials, build headers or write a script.
Call a tool with `tool_call`, passing its exact name below as `id` and its
arguments as `args`. You do not need `tool_search` or `tool_describe`.

## Which environment

The server runs against one environment: mainnet when both
`CRED_PARADIGM_MAINNET_ACCESS` and `CRED_PARADIGM_MAINNET_SIGNING` are in your
environment, otherwise testnet. Check those two names before a state-changing
call and say which environment it is. The machine's hostname says nothing
about it: a box named testnet can hold a mainnet key.

## Tools

`?` marks an optional argument. Every tool calls a path under `/v2/drfq/`,
shown after the arrow.

### Credentials

- `paradigm__paradigm_echo(payload?)` → `GET` or `POST /echo/`: a 200 means
  both credentials and the signature are right. It changes nothing. Pass a
  JSON object as `payload` to test a signed body.

### Reference data

- `paradigm__paradigm_drfqv2_instruments(instrument_id?, venue?, base_currency?, kind?, margin_kind?, state?, venue_instrument_name?, include_greeks?, cursor?, page_size?)` → `/instruments/`: list, or one instrument by id
- `paradigm__paradigm_drfqv2_counterparties(venue?, cursor?, page_size?)` → `/counterparties/`: maker desks with their `groups` and `venues`. With `venue` the tool reads every page itself and returns `{results, count, venue, scanned, truncated}`

### RFQs

- `paradigm__paradigm_drfqv2_rfqs(state?, role?, venue?, strategies?, product_codes?, cursor?, page_size?)` → `/rfqs/`: list
- `paradigm__paradigm_drfqv2_rfq_snapshot(rfq_id)` → `/rfqs/{id}/`, `/bbo/`, `/orders/`: one RFQ, its BBO and its book in one call
- `paradigm__paradigm_drfqv2_create_rfq(venue, legs, quantity, counterparties?, is_taker_anonymous?, account_name?, label?, state?)` → `POST /rfqs/`: create an RFQ

### Orders

- `paradigm__paradigm_drfqv2_orders(order_id?, rfq_id?, state?, venue?, currency?, base_currency?, cursor?, page_size?)` → `/orders/`: list, or one order by id
- `paradigm__paradigm_drfqv2_post_order(rfq_id, side, quantity, type?, time_in_force?, price?, legs?, account_name?, label?, order_id?)` → `POST /orders/`, or `PUT /orders/{id}/` when `order_id` is set: quote, cross or amend
- `paradigm__paradigm_drfqv2_cancel(target, rfq_id?, order_id?, state?, venue?, currency?, base_currency?)` → `DELETE`: `target` `rfq` cancels `rfq_id`, `order` cancels `order_id`, `orders` cancels every order matching the filters

### Trades and pricing

- `paradigm__paradigm_drfqv2_trades(trade_id?, mode?, state?, venue?, strategies?, product_codes?, cursor?, page_size?)` → `/trades/`, or `/trade_tape/` with `mode: "tape"`: your trades, one trade, or the public tape. `state` filters your trades only and `strategies` filters the tape only. The tool drops the other one without an error
- `paradigm__paradigm_drfqv2_price_legs(legs, bid_price?, ask_price?, accept_estimated_prices?, algorithm_version?)` → `POST /pricing/`: split a structure price across its legs
- `paradigm__paradigm_drfqv2_mmp(action?)` → `/mmp/status/`: `action` `status` (the default) reads the circuit-breaker, `reset` re-arms it

### Shapes

- A leg on `create_rfq` is `{instrument_id, ratio, side, price?}`.
- A leg on `post_order` is `{instrument_id, price}`.
- A leg on `price_legs` is `{instrument_id, ratio, side}`.
- `instrument_id` is an integer in a leg and a string in
  `paradigm_drfqv2_instruments(instrument_id)`. `ratio`, `price` and `quantity` are decimal
  strings.
- `side` is `BUY` or `SELL`. `type` is `LIMIT` (the default) or `HIDDEN`.
- `time_in_force` is `GOOD_TILL_CANCELED` (the default, a maker quote) or
  `FILL_OR_KILL` (a taker cross).
- `venue` is `PRDX`, `DBT`, `BYB` or `BIT` on most tools. `create_rfq` and
  `rfqs` accept a longer list.
- RFQ `state` is `OPEN`, `CLOSED` or `DRAFT`, and `create_rfq` takes `OPEN`
  (the default) or `DRAFT`. Order `state` is `OPEN`, `CLOSED` or `PENDING`.
  Trade `state` is `COMPLETED`, `PENDING` or `REJECTED`.
- `role` is `MAKER` or `TAKER`. Instrument `kind` is `FUTURE` or `OPTION`,
  `margin_kind` is `INVERSE` or `LINEAR`, instrument `state` is `ACTIVE` or
  `EXPIRED`.
- `page_size` is 1 to 1000. A list answers `{count, next, results}`. Pass
  `next` back as `cursor` with the same filters.

## Venue facts

- **Pass `counterparties` on every `create_rfq`.** The tool defaults it to an
  empty list, and Paradigm answers `At least one counterparty must be
  specified`.
- **Pass `account_name` on `create_rfq` and on a maker quote.** The tool
  marks it optional, and Paradigm answers `This field is required` without it.
  A taker cross may leave it out.
- `is_taker_anonymous` defaults to `true`, which needs at least three LP
  counterparties.
- `cancel` with `target: "orders"` and no filter cancels every DRFQ order on
  the desk. Fill in every filter you mean, and confirm with the user when you
  cannot.
- An order comes back `PENDING`. Read its final state with
  `paradigm_drfqv2_orders(order_id=...)`.
- These tools cannot pick a desk on a multi-desk key. Paradigm uses the key's
  default desk, so say which desk that is before a state-changing call when
  the user has more than one.
- There is no live stream here. Poll `paradigm_drfqv2_rfq_snapshot` every 1 to
  3 seconds for quotes.

## When there are no `paradigm__` tools

The server did not start for this account. Tell the user which of these
applies, cheapest to check first:

- The change has not reached the agent yet. Ask the user to reload, or retry
  shortly.
- One of the two credentials is not enrolled. The server needs the access key
  and the signing key in the same environment.
- A credential was enrolled with a custom host or a custom label. Only a
  credential named `paradigm-mainnet-access`, `paradigm-testnet-signing` and so
  on, for one of the app's environments, starts the server.
- Another MCP server is already named `paradigm`.

## When a call fails

A failed call returns the status, the path, Paradigm's request id when
Paradigm answered, and a hint. Quote the request id when you report a
failure. The hint names server settings you cannot change, so read the table
below. `paradigm_drfqv2_rfq_snapshot` returns a failed part as an error
object in its place, and the other parts still come back.

| Symptom | Causes, cheapest first |
|---|---|
| `paradigm_echo` fails with 401 | The access key and the signing key come from different Paradigm keys. A clock more than 30 seconds off Paradigm's. |
| 403 naming a placeholder or an `X-Dime-Sign-` header | The proxy refused the request. Ask the user to reload, and to enrol the credential again if it repeats. |
| 403 `Invalid API Access Key` | The access key reached Paradigm unsubstituted. The access key was revoked, or it is for the other environment. |
| 400 | The signature was accepted. Paradigm is objecting to the arguments, and the body says which. |
| 404 | An id that does not exist in this environment. |
