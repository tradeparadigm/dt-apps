---
name: deribit-api
description: >
  Trading and account access on Deribit's v2 API using a client secret held by
  the DIME credential proxy. Covers the whole flow: exchanging the stored client
  secret for an access token, reading instruments, order book and index prices,
  reading positions and account summary, placing and cancelling orders on
  options, futures and perpetuals, and authenticating a WebSocket. Use for ANY
  request to www.deribit.com or test.deribit.com — including "buy a BTC call on
  Deribit", "what are my Deribit positions", "cancel my Deribit orders", "what
  is my Deribit margin", or a 403 from the credential proxy on a Deribit call.
  Read this BEFORE calling Deribit: the token exchange is the only call that
  touches the credential, and it must be a GET with the placeholder in the query
  string.
metadata:
  author: tradeparadigm
---

# Deribit

Deribit is a crypto derivatives exchange. Its main market is BTC and ETH
options; it also lists perpetuals and dated futures. Everything is margined in
the underlying coin or in USDC, depending on the instrument.

Your `AGENTS.md` already explains the placeholder mechanism in general — how
`CRED_<NAME>` and `CRED_<NAME>_META` work and what the `cred-` prefix means.
This file assumes that and covers only what is specific to Deribit.

## Hosts

| Environment | REST base | WebSocket |
|---|---|---|
| Mainnet | `https://www.deribit.com/api/v2` | `wss://www.deribit.com/ws/api/v2` |
| Testnet | `https://test.deribit.com/api/v2` | `wss://test.deribit.com/ws/api/v2` |

Check which one your credential is scoped to before assuming mainnet. Testnet
is a full, separate exchange with its own accounts and its own API keys.

## What you hold

- **`CRED_DERIBIT_<ENV>_SECRET`** — the client secret, as a `cred-` placeholder.
  You never see the secret itself.
- **`CRED_DERIBIT_<ENV>_SECRET_META`** — JSON, containing `client_id`. Not a
  secret; you send it in the clear.

The exact variable names depend on the label chosen at enrolment; read them from
the environment rather than assuming.

## Deribit does not sign requests

This is the difference from Bybit and OKX, and it makes everything simpler.
There is no HMAC, no timestamp, no canonical string. You make **one** call that
carries the credential, get a bearer token back, and use that token on
everything else.

```
credential proxy holds the client secret
        │
        │  GET /api/v2/public/auth   ← the only call with a placeholder
        ▼
   access_token  (yours, expires in ~900s)
        │
        │  Authorization: Bearer <access_token>
        ▼
   every other private call
```

## Step 1: get an access token

```
GET /api/v2/public/auth
    ?grant_type=client_credentials
    &client_id=<client_id from _META>
    &client_secret=<the cred- placeholder>
```

Send it as a **GET with the parameters in the query string**. That is the only
shape the credential proxy is watching: it swaps the placeholder for the real
secret in the query string of this one path and forwards the request.

The response:

```json
{
  "result": {
    "access_token": "1580293819...",
    "expires_in": 900,
    "refresh_token": "1580293819...",
    "token_type": "bearer",
    "scope": "account:read_write trade:read_write"
  }
}
```

Keep `access_token` in memory. Note `expires_in` and re-authenticate before it
runs out.

**Do not use the `refresh_token` grant.** It hits this same
`/api/v2/public/auth` path without a client secret, and the proxy refuses any
request to that path that does not carry the placeholder — you will get a 403
rather than a token. Just run the `client_credentials` exchange again. It is one
request and it always works.

Check the `scope` in the response. A key created read-only comes back with
`trade:read` rather than `trade:read_write`, and every order you place will be
rejected. That is a Deribit key setting, not something to work around — report
it.

## Step 2: everything else

```
Authorization: Bearer <access_token>
```

No placeholder, no proxy involvement. The proxy narrows this app to
`/api/v2/public/auth` alone, so every other Deribit path is forwarded untouched.

If a private call returns `13009 unauthorized`, your token expired. Go back to
step 1.

## Market data (public, no token needed)

```
GET /api/v2/public/get_instruments?currency=BTC&kind=option&expired=false
GET /api/v2/public/get_order_book?instrument_name=BTC-PERPETUAL&depth=10
GET /api/v2/public/ticker?instrument_name=BTC-PERPETUAL
GET /api/v2/public/get_index_price?index_name=btc_usd
GET /api/v2/public/get_tradingview_chart_data?instrument_name=BTC-PERPETUAL&start_timestamp=...&end_timestamp=...&resolution=60
```

Deribit instrument names are readable and encode everything:

| Name | What it is |
|---|---|
| `BTC-PERPETUAL` | BTC perpetual, coin-margined |
| `BTC-27MAR26` | BTC future expiring 27 March 2026 |
| `BTC-27MAR26-80000-C` | BTC call, strike 80000, expiring 27 March 2026 |
| `BTC-27MAR26-80000-P` | the matching put |
| `BTC_USDC-PERPETUAL` | USDC-margined perpetual |

`kind` in `get_instruments` is `future`, `option`, `spot`, `future_combo` or
`option_combo`. Always pass `expired=false` unless historical instruments are
genuinely wanted — the expired list is very long.

For an option, `public/ticker` carries the greeks, the mark IV and the
underlying index price. Use it rather than computing anything yourself.

## Account state (token required)

```
GET /api/v2/private/get_account_summary?currency=BTC&extended=true
GET /api/v2/private/get_positions?currency=BTC&kind=option
GET /api/v2/private/get_open_orders?currency=BTC
GET /api/v2/private/get_order_history_by_currency?currency=BTC&count=50
GET /api/v2/private/get_user_trades_by_currency?currency=BTC&count=50
```

`get_account_summary` with `extended=true` is the one to read first: it carries
`equity`, `available_funds`, `initial_margin`, `maintenance_margin` and
`delta_total`. Check `available_funds` before sizing an order — Deribit's
margin for options is portfolio-based and not something to estimate.

`currency` is required nearly everywhere and is the settlement currency: `BTC`,
`ETH`, `USDC`, `USDT`, or `any`.

## Placing an order

Deribit uses separate buy and sell endpoints rather than a `side` parameter.

```
GET /api/v2/private/buy
    ?instrument_name=BTC-PERPETUAL
    &amount=10
    &type=limit
    &price=60000
    &label=myorder1
    &post_only=true
```

```
GET /api/v2/private/sell?instrument_name=BTC-27MAR26-80000-C&amount=1&type=limit&price=0.05
```

These are `GET` requests with query parameters — Deribit's HTTP interface is
JSON-RPC mapped onto GET, which surprises people. They are not idempotent
despite the verb.

- **`amount` units differ by instrument.** For BTC futures and perpetuals it is
  in **USD** and must be a multiple of 10. For options it is in the **base
  coin** (contracts of 1 BTC or 1 ETH). For USDC-margined instruments it is in
  the base coin. Read `contract_size` and `min_trade_amount` from
  `get_instruments` rather than guessing — this is the single most common way
  to place an order a hundred times the intended size.
- **`price` for options is quoted in the base coin**, not in dollars. A BTC call
  at `0.05` costs 0.05 BTC per contract.
- `type`: `limit`, `market`, `stop_limit`, `stop_market`.
- `post_only=true` for a maker-only order; Deribit will reprice rather than
  cross. Add `reject_post_only=true` if you would rather it be rejected than
  repriced.
- `reduce_only=true` to close rather than open.
- `label` is your own id, up to 64 characters. Set it — it is how you cancel by
  label and how you find the order again.
- `time_in_force`: `good_til_cancelled` (default), `fill_or_kill`,
  `immediate_or_cancel`.

The response's `result.order.order_state` tells you what happened: `open`,
`filled`, `rejected`, `cancelled`. A `200` with `order_state: "rejected"` is a
failure — read `result.order.reject_reason`.

## Cancelling

```
GET /api/v2/private/cancel?order_id=ETH-12345
GET /api/v2/private/cancel_by_label?label=myorder1&currency=BTC
GET /api/v2/private/cancel_all_by_instrument?instrument_name=BTC-PERPETUAL
GET /api/v2/private/cancel_all?detailed=false
```

`cancel_all` with no arguments cancels every open order on the whole account,
across currencies and instruments. Scope it to an instrument or a currency
unless that is genuinely what was asked for.

## WebSocket

Deribit's WebSocket carries the **same JSON-RPC methods** as the HTTP interface,
and this is the one venue in this catalogue where private streams work.

```
wss://www.deribit.com/ws/api/v2
```

Authenticate by sending `public/auth` as the first frame — but **with the access
token you already obtained over HTTP**, not with the client secret:

```json
{"jsonrpc":"2.0","id":1,"method":"public/auth",
 "params":{"grant_type":"client_credentials","client_id":"...","client_secret":"..."}}
```

You cannot send that, because you do not have the client secret and the proxy
does not rewrite WebSocket frames. Instead, take the token from the HTTP
exchange in step 1 and send:

```json
{"jsonrpc":"2.0","id":1,"method":"private/subscribe",
 "params":{"channels":["user.orders.BTC-PERPETUAL.raw"],"access_token":"<access_token>"}}
```

Deribit accepts `access_token` as a parameter on private WebSocket methods,
which is what makes this work where Bybit and OKX do not. Useful channels:
`user.orders.<instrument>.raw`, `user.trades.<currency>.raw`,
`user.portfolio.<currency>`, and the public `ticker.<instrument>.100ms` and
`book.<instrument>.none.10.100ms`.

Re-run the HTTP exchange and re-subscribe when the token expires.

## When something fails

- **403 from the credential proxy**, body naming `placeholder_absent` — you
  called `/api/v2/public/auth` without the placeholder in the query string.
  Most likely you tried the `refresh_token` grant; use `client_credentials`.
- **`13004 invalid_credentials`** — the enrolled client secret does not match
  the `client_id` you sent, or the key was revoked on Deribit's side.
- **`13009 unauthorized`** — the access token expired or was not sent. Re-run
  step 1.
- **`11044 not_open_order`** — the order was already filled or cancelled.
- **`10001 ... amount`** — almost always the `amount` units: USD and a multiple
  of 10 for BTC futures, base coin for options.
- **An order rejected with `post_only_reject`** — you sent
  `reject_post_only=true` and the order would have crossed. Re-price it.

## Reference

Full API documentation: https://docs.deribit.com/

Go there for anything this file does not cover — the complete method list,
rate limits, the full error code table. This file is the part Deribit's own docs
cannot tell you: that the client secret is not yours to hold, and what to send
instead.
