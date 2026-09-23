---
name: okx-api
description: >
  Trading and account access on OKX's v5 API using an API key, passphrase and
  secret key held by the DIME credential proxy. Covers the whole flow: building
  OKX's signed string, reading instruments, tickers and orderbook, reading
  balances and positions, placing, amending and cancelling orders, demo
  trading, and what to do about private WebSocket streams. Use for ANY request
  to www.okx.com — including "buy BTC on OKX", "what is my OKX balance",
  "cancel my OKX orders", "show my OKX positions", or a 403 from the credential
  proxy on an OKX call. Read this BEFORE calling OKX: the signed string covers
  the request path INCLUDING its query string and uses a timestamp format OKX
  will reject if you use the obvious one.
metadata:
  author: tradeparadigm
---

# OKX

OKX is a centralised exchange. One API — v5 — serves spot, margin, perpetual
swaps, futures and options; which one you are trading is determined by the
instrument id you pass, not by a different endpoint.

Your `AGENTS.md` already explains the placeholder mechanism in general — how
`CRED_<NAME>` and `CRED_<NAME>_META` work, what the `sign-` prefix means, and
how to name the `X-Dime-Sign-` header. This file assumes that and covers only
what is specific to OKX.

## Host

```
https://www.okx.com
```

One host for everything, live and demo alike.

## What you hold, and what you send

OKX authenticates with three values. **You hold one of them and never see the
other two.**

| Value | How it reaches OKX | What you do |
|---|---|---|
| API key | Proxy attaches `OK-ACCESS-KEY` | nothing |
| Passphrase | Proxy attaches `OK-ACCESS-PASSPHRASE` | nothing |
| Secret key | Proxy signs with it | send a `sign-` placeholder + the bytes to sign |

So a private request from you carries **two** headers you set yourself and one
placeholder:

```
OK-ACCESS-TIMESTAMP: 2026-01-01T00:00:00.000Z
OK-ACCESS-SIGN:      <the sign- placeholder>
Content-Type:        application/json
```

Do **not** try to set `OK-ACCESS-KEY` or `OK-ACCESS-PASSPHRASE`. You do not have
those values, and the proxy adds them on the way out.

If the credential was enrolled but a call still fails on authentication, check
that all three OKX credentials are enrolled. A private call with any one of the
three missing is rejected by OKX, and the failure does not say which one.

## The signed string

```
timestamp + method + requestPath + body
```

concatenated with no separators, where:

- **`timestamp`** is ISO 8601 in UTC with **milliseconds and a trailing `Z`** —
  `2026-01-01T00:00:00.000Z`. It must be the byte-for-byte same string you put
  in the `OK-ACCESS-TIMESTAMP` header. A Unix epoch timestamp is rejected, and
  so is an ISO string without the milliseconds.
- **`method`** is the HTTP verb in **upper case**: `GET`, `POST`.
- **`requestPath`** is the path **including the query string** — for a GET that
  is `/api/v5/account/balance?ccy=USDT`, not `/api/v5/account/balance`. This is
  the most common mistake.
- **`body`** is the raw JSON body for a POST, byte for byte as you will send
  it, and the **empty string** for a GET.

The result is base64, not hex. Bybit uses hex; OKX does not. The credential
template already sets this, so you do not have to — but if you are comparing
against a Bybit example, that is the difference.

### Worked example

Read your USDT balance.

```
GET /api/v5/account/balance?ccy=USDT
```

1. Take an ISO 8601 UTC timestamp with milliseconds: `2026-01-01T00:00:00.000Z`.
   Put it in `OK-ACCESS-TIMESTAMP`.
2. Build the signed string — timestamp, method, path with query, empty body:

   ```
   2026-01-01T00:00:00.000ZGET/api/v5/account/balance?ccy=USDT
   ```

3. Base64-encode **those bytes** and send them in `X-Dime-Sign-<label>`, where
   `<label>` is the secret credential's label. (This base64 is the transport for
   the payload. The base64 of the signature itself is separate, and the proxy
   does it.)
4. Put the `sign-` placeholder in `OK-ACCESS-SIGN`.

The proxy computes HMAC-SHA256 over your bytes with the secret, base64-encodes
the digest, writes it into `OK-ACCESS-SIGN` in place of the placeholder, adds
`OK-ACCESS-KEY` and `OK-ACCESS-PASSPHRASE`, strips the `X-Dime-Sign-` header,
and forwards. OKX sees an ordinary signed OKX request.

**Serialise a POST body once.** Sign the exact string you send. If you build the
JSON for signing and let an HTTP library re-serialise it when sending — reordered
keys, different spacing, a float rendered differently — OKX recomputes over what
it received and you get `50113 Invalid Sign`.

## Which calls need the credentials

The proxy attaches the key and passphrase, and demands the signature
placeholder, on exactly these paths:

```
/api/v5/trade/*        /api/v5/account/*       /api/v5/asset/*
/api/v5/users/*        /api/v5/subaccount/*    /api/v5/copytrading/*
```

Everything under `/api/v5/market/*` and `/api/v5/public/*` is open. Send those
with no auth headers, no timestamp and no signature. The proxy is not watching
those paths, so a placeholder sent to one goes to OKX verbatim.

## Market data (public, unsigned)

```
GET /api/v5/public/instruments?instType=SWAP
GET /api/v5/market/ticker?instId=BTC-USDT-SWAP
GET /api/v5/market/books?instId=BTC-USDT-SWAP&sz=20
GET /api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=100
```

`instId` is OKX's instrument name and encodes the product:

| Shape | Product |
|---|---|
| `BTC-USDT` | spot |
| `BTC-USDT-SWAP` | perpetual swap |
| `BTC-USD-260327` | dated future |
| `BTC-USD-260327-80000-C` | option |

Check `/api/v5/public/instruments` before your first order on an instrument: it
carries `lotSz`, `tickSz` and `minSz`, and an order that is not a multiple of
those is rejected.

`ordType` prices and sizes are in **contracts** for SWAP and FUTURES, not in the
base coin. `ctVal` on the instrument says how much one contract is worth. This
catches people out — "buy 1 BTC-USDT-SWAP" is one contract, not one bitcoin.

## Account state (signed)

```
GET /api/v5/account/balance
GET /api/v5/account/balance?ccy=USDT
GET /api/v5/account/positions?instType=SWAP
GET /api/v5/account/config                      # account mode, position mode
GET /api/v5/trade/orders-pending?instType=SWAP  # working orders
GET /api/v5/trade/orders-history?instType=SWAP&limit=50
GET /api/v5/trade/fills?instType=SWAP&limit=50
```

`/api/v5/account/config` is worth reading once before trading: it tells you the
account's `acctLv` (cash, single-currency margin, multi-currency margin,
portfolio margin) and `posMode` (`net_mode` or `long_short_mode`), and the
correct order parameters differ between them.

## Placing an order

```
POST /api/v5/trade/order
Content-Type: application/json
```

```json
{
  "instId": "BTC-USDT-SWAP",
  "tdMode": "cross",
  "side": "buy",
  "ordType": "limit",
  "px": "60000",
  "sz": "1",
  "clOrdId": "myorder1"
}
```

- `tdMode` is required: `cash` for spot, `cross` or `isolated` for margin and
  derivatives. Getting this wrong is the most common rejection.
- `side` is `buy` or `sell`, lower case.
- `ordType`: `limit`, `market`, `post_only`, `fok`, `ioc`.
- `px` and `sz` are **strings**. A market order omits `px`.
- `clOrdId` is your own id — letters and digits only, up to 32 characters, **no
  hyphens**. Set it: it is how you cancel without first looking up OKX's
  `ordId`, and how a retry after a timeout avoids a duplicate.
- In `long_short_mode` you must also send `posSide` (`long` or `short`). In
  `net_mode` you must not.

**A 200 does not mean the order was placed.** OKX answers with an envelope:

```json
{"code":"0","msg":"","data":[{"ordId":"...","clOrdId":"myorder1","sCode":"0","sMsg":""}]}
```

Check the **outer `code`** (`"0"` is success) and then the **per-order `sCode`**
inside `data`. A batch can return outer `code: "1"` with some orders accepted;
`sCode` is the one that says what happened to your order.

## Amending and cancelling

```
POST /api/v5/trade/amend-order   {"instId":"BTC-USDT-SWAP","clOrdId":"myorder1","newPx":"59000"}
POST /api/v5/trade/cancel-order  {"instId":"BTC-USDT-SWAP","clOrdId":"myorder1"}
POST /api/v5/trade/cancel-batch-orders  [{"instId":"BTC-USDT-SWAP","clOrdId":"myorder1"}]
```

All are `POST`. `instId` is required alongside the order id. There is no
"cancel everything" endpoint that takes no arguments — list working orders with
`/api/v5/trade/orders-pending` and cancel them in a batch.

## Demo trading

OKX's demo environment is the **same host** with one extra header:

```
x-simulated-trading: 1
```

Demo API keys are different keys from live keys, created separately in OKX's
demo section. They enrol exactly the same way.

Because both live and demo use `www.okx.com`, **only one OKX credential set can
be enrolled at a time**. Two sets scoped to the same host make each other's
requests fail — each one's signing rule demands its own placeholder on the same
paths. If both a live and a demo set appear to be enrolled, say so rather than
guessing which is in effect.

## WebSocket

**Public streams work.** `wss://ws.okx.com:8443/ws/v5/public` needs no
authentication — subscribe and read.

**Private streams do not work through the credential proxy.** OKX authenticates
`wss://ws.okx.com:8443/ws/v5/private` with a `login` frame sent after the
connection opens, carrying a signature over `timestamp + 'GET' + '/users/self/verify'`.
The proxy signs HTTP requests; it does not sign WebSocket frames.

For order updates, fills and position changes, poll the signed REST endpoints —
`/api/v5/trade/orders-pending` for working orders, `/api/v5/trade/fills` for
fills. Say so if asked to stream private data: it is a real limitation, not
something to work around by asking for the secret.

## When something fails

- **403 from the credential proxy**, body naming `placeholder_absent` — you
  called a signed path without putting the placeholder in `OK-ACCESS-SIGN`. The
  body lists where it looked.
- **`50113 Invalid Sign`** — the string you signed is not what OKX
  reconstructed. In order of likelihood: the query string was left off
  `requestPath`; the timestamp in the header differs from the one you signed;
  the body was re-serialised after signing; the method was lower case.
- **`50102 Timestamp request expired`** — your timestamp is more than 30 seconds
  from OKX's clock, or it is not in ISO 8601 with milliseconds.
- **`50111 Invalid OK-ACCESS-KEY` / `50105 Invalid OK-ACCESS-PASSPHRASE`** — the
  enrolled key or passphrase is wrong, or one of the three credentials was not
  enrolled. You cannot inspect them; report which call failed and let the user
  re-enrol.
- **`51000 Parameter … error`** — usually `tdMode` missing or wrong for the
  instrument type, or `posSide` sent in `net_mode` (or omitted in
  `long_short_mode`).

## Reference

Full API documentation: https://www.okx.com/docs-v5/en/

Go there for anything this file does not cover — full parameter lists, rate
limits, the complete error code table. This file is the part OKX's own docs
cannot tell you: that the key, passphrase and secret are not yours to hold, and
what to send instead.
