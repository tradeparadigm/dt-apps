---
name: binance-api
description: >
  Trading and account access on Binance spot and USD-M futures using an API key
  and secret held by the DIME credential proxy. Covers the whole flow: building
  the signed query string, reading balances, positions and orders, placing,
  amending and cancelling orders on both products, and the places where spot and
  futures disagree. Use for ANY request to api.binance.com or fapi.binance.com —
  including "buy BTC on Binance", "what is my Binance balance", "close my
  Binance position", "my USD-M futures PnL", or a 403 from the credential proxy
  on a Binance call. Read this BEFORE calling Binance: the signature covers the
  bytes of the query string as transmitted, and the encoding rules changed in
  2026.
metadata:
  author: tradeparadigm
---

# Binance

Two products behind one skill, and they are not the same API.

| Product | Host | Paths |
|---|---|---|
| Spot | `api.binance.com` | `/api/v3/*`, `/sapi/v1/asset/*` |
| USD-M futures | `fapi.binance.com` | `/fapi/v1/*`, `/fapi/v2/*`, `/fapi/v3/*` |
| Spot — demo | `demo-api.binance.com` | same paths |
| USD-M futures — demo | `demo-fapi.binance.com` | same paths |

Demo keys are minted at `demo.binance.com` and work **only** against the demo
hosts; live keys work only against the live ones. Check which environment the
credential is scoped to before assuming a host.

Your `AGENTS.md` explains the placeholder mechanism in general — how
`CRED_<NAME>` works, what `sign-` means, and how to name the `X-Dime-Sign-`
header. This file covers only what is specific to Binance.

## If `cex` is on your PATH

Run `command -v cex` first. If it prints a path, use `cex` for anything it
covers and skip the signing in the rest of this file. It finds your Binance
credential, takes the environment from the hosts the credential lists, and
signs through the proxy.

```sh
cex balance
cex positions
cex orders --symbol BTCUSDT
cex fills --symbol BTCUSDT --from <unix ms>
cex income --from <unix ms>
cex place --symbol BTCUSDT --side buy --type limit --amount 0.002 --price <price>
cex cancel --id <orderId>
```

It covers USD-M futures, live and demo. Spot is REST only. `cex --help` lists every flag.

Each run prints one JSON document, and `--help` prints the usage.

- `"ok": true`, exit 0: `data` is the answer.
- `"ok": false`, exit 1: the venue or the proxy refused, or nothing answered.
  - `refused` lists each refused request with its status and body. Read them
    against "When something fails" below; the causes are the same as for a REST call.
  - `error` with `status` and `body`: one call failed outright. Read it the
    same way.
  - `errors`: what the client itself reported, sometimes with no `refused`
    entry. `data` is whatever came back anyway.
  - `could not load binance markets`: the market list did not load. `refused`
    names the request. A proxy 403 or the venue being down are the usual
    causes.
  - `no answer within 60s`: the venue or the proxy is slow or unreachable. Run
    it once more with `--timeout 120`, then report it.
- exit 2: the command was wrong, and nothing was changed.
  - An unknown symbol. It suggests close matches.
  - A missing or malformed flag.
  - Several venue credentials. Pass `--cred CRED_<NAME>`. A `--cred` that names
    no `sign-` credential, or one whose hosts `cex` does not know, is refused
    the same way.
  - No `sign-` credential it can use. None is enrolled, the one enrolled is a
    read-only `cred-` token, or its `_META` has no `_hosts` because the
    terminal predates them. Use REST.
  - `no open order matches`: the order already filled or was cancelled, or the
    id is wrong. `cex orders` shows what is open.

For anything `cex` does not cover, or when it is not installed, use the REST
instructions below. If `cex` fails in a way this list does not explain, say
what you ran and what it printed, then use REST.

## What you hold, and what you do not

**You never send an API key.** Binance's signature does not cover the key, so
the key stays sealed and the proxy attaches `X-MBX-APIKEY` itself on every
signed path. Do not set that header, and do not go looking for the key.

What you do hold is the signing placeholder — `CRED_BINANCE_<ENV>_SECRET`, a
`sign-` value. You put it where the signature goes and tell the proxy what to
sign.

## Every signed request

Run the client the app ships, at `scripts/binance.mjs` beside this file.

```sh
METHOD=GET TARGET='/fapi/v3/balance?recvWindow=5000' node scripts/binance.mjs

METHOD=GET TARGET='/api/v3/account' node scripts/binance.mjs

METHOD=POST TARGET='/fapi/v1/order?symbol=BTCUSDT&side=BUY&type=LIMIT&timeInForce=GTC&quantity=0.002&price=50000' node scripts/binance.mjs
```

`TARGET` is the path and its query, with no `signature` and no `timestamp`. The
client adds the timestamp, percent-encodes the query, signs that exact string
and appends the placeholder last. Binance takes parameters in the query string
on every method, so a POST puts them there too.

It picks the host from the path: `/fapi` is USD-M futures, `/api` is spot. It
cannot tell demo from live that way, so when the credential's name does not say,
it refuses and asks you to set `BINANCE_HOST`. Guessing there would put a live
order on a demo key's account.

You never send an API key. Binance's signature does not cover it, so the proxy
attaches `X-MBX-APIKEY` itself on every signed path. Do not set that header.

How the signing works is in [references/signing.md](references/signing.md).
Read it when a call fails, not before.

### When this client is wrong

Check `~/.openclaw/workspace/tools/binance/errata-1.2.0.md` before your first
call. An earlier chat writes it when this client turns out to be wrong against
the live venue. The version in the name is this app's version, so a file naming
any other version is stale: delete it and ignore what it said.

When the client is wrong and no errata covers it, fix your own call, get the
user their answer, then write that file yourself. Say what failed, how you knew,
and what you changed. Tell the user in one line that you did.

Never edit `scripts/binance.mjs`. A publish replaces it, so an edit there is
lost and the next chat repeats your work.


## Which calls need signing

The proxy demands the placeholder on the private paths only — every
`/api/v3` order, account, trade and allocation path, `/sapi/v1/asset/*` and
`/sapi/v3/asset/*`, and the `/fapi` order, position, account and income
families. A request to one of them without the placeholder is refused with 403
before it leaves the building.

Public market data is **not** in that set and must be sent with no auth at all:

```
GET /api/v3/exchangeInfo?symbol=BTCUSDT          GET /fapi/v1/exchangeInfo
GET /api/v3/depth?symbol=BTCUSDT&limit=100       GET /fapi/v1/depth
GET /api/v3/klines?symbol=BTCUSDT&interval=1h    GET /fapi/v1/klines
GET /api/v3/ticker/price?symbol=BTCUSDT          GET /fapi/v1/premiumIndex
```

Adding a signature to a public call is not harmless: the proxy is not watching
those paths, so your placeholder would reach Binance verbatim.

## Where spot and futures disagree

Read this before assuming one answer works on both.

| | Spot | USD-M futures |
|---|---|---|
| `signature` position | any | **must be last** |
| Security classes | NONE, TRADE, USER_DATA, USER_STREAM | plus **MARKET_DATA** and USER_STREAM, which need the key but **no signature** |
| Notional filter | `NOTIONAL` (min and max) and legacy `MIN_NOTIONAL` | `MIN_NOTIONAL` only, field `notional` |
| Account read | one `/api/v3/account` | `/fapi/v2/account` and `/fapi/v3/account` both live, same for `balance` and `positionRisk` |
| REST listen key | **gone** | `/fapi/v1/listenKey` still works |

## Private WebSocket streams

**Spot's REST listen key no longer exists.** `POST /api/v3/userDataStream` was
retired on 2026-02-20 and answers **410 Gone**. Spot user data now comes from
the WebSocket API (`userDataStream.subscribe`), which this credential does not
cover. Poll the signed REST endpoints instead, and say so rather than
inventing an endpoint.

**Futures still has one.** `POST /fapi/v1/listenKey` needs the API key and no
signature — the proxy attaches the key for you — and returns a key you connect
with at `wss://fstream.binance.com/ws/<listenKey>`.

## Market data (public, unsigned)

```
GET /api/v3/exchangeInfo?symbol=BTCUSDT     # filters: PRICE_FILTER, LOT_SIZE, NOTIONAL
GET /fapi/v1/exchangeInfo                   # filters: PRICE_FILTER, LOT_SIZE, MIN_NOTIONAL
```

Check the filters before a first order on a symbol. `tickSize` and `stepSize`
are what an order is rejected for, and the notional minimum is what a small
order is rejected for.

## Account state (signed)

```
GET /api/v3/account?timestamp=…                     # spot balances
GET /api/v3/openOrders?symbol=BTCUSDT&timestamp=…
GET /api/v3/myTrades?symbol=BTCUSDT&timestamp=…
GET /fapi/v3/balance?timestamp=…                    # futures wallet
GET /fapi/v3/account?timestamp=…                    # futures account + positions
GET /fapi/v3/positionRisk?timestamp=…               # open positions
GET /fapi/v1/userTrades?symbol=BTCUSDT&timestamp=…
GET /fapi/v1/income?timestamp=…                     # funding, realised PnL
```

`symbol` is mandatory on the spot per-symbol reads (`myTrades`, `allOrders`);
omitting it is `-1102`, not an empty list.

## Placing an order

Spot:

```
POST /api/v3/order?symbol=BTCUSDT&side=BUY&type=LIMIT&timeInForce=GTC
     &quantity=0.001&price=40000&newClientOrderId=my-1&timestamp=…&signature=<placeholder>
```

USD-M futures:

```
POST /fapi/v1/order?symbol=BTCUSDT&side=BUY&type=LIMIT&timeInForce=GTC
     &quantity=0.002&price=40000&newClientOrderId=my-1&timestamp=…&signature=<placeholder>
```

- `newClientOrderId` is your own id. Set it — it is how you cancel or query
  without first learning Binance's `orderId`, and how a retry after a timeout
  avoids placing twice.
- A market order omits `price` and `timeInForce`.
- `reduceOnly=true` closes rather than opens, on futures.
- Futures positions depend on account mode: in hedge mode `positionSide` is
  required (`LONG`/`SHORT`), in one-way mode it must be omitted. Read
  `/fapi/v1/positionSide/dual` if unsure.

Cancelling:

```
DELETE /api/v3/order?symbol=BTCUSDT&origClientOrderId=my-1&timestamp=…&signature=<placeholder>
DELETE /fapi/v1/order?symbol=BTCUSDT&origClientOrderId=my-1&timestamp=…&signature=<placeholder>
DELETE /api/v3/openOrders?symbol=BTCUSDT&timestamp=…      # every open order on a symbol
```

## When something fails

Tell the layers apart by the shape of the response, not by guessing.

- **403 with a plain-text body** is the credential proxy. The body names the
  placeholder and lists exactly where it looked. The request never reached
  Binance. Read it and retry.
- **A JSON body with a negative `code`** is Binance.
- **A key or signature error from Binance can mean the proxy substituted
  nothing.** When the host or path is outside what your credential covers, the
  proxy forwards the request untouched and your placeholder arrives at Binance
  as a literal string. Binance then complains about the key or the signature,
  and names neither the host nor the credential. Check those before you touch
  the request.
- **HTTP 429 / 418** are rate limits: 429 is a violation, 418 is an IP ban for
  continuing after 429s. Bans scale from 2 minutes to 3 days and carry
  `Retry-After`. Back off; do not retry in a loop.

| code | meaning |
|---|---|
| `-1021` | timestamp outside `recvWindow`, or more than 1s ahead of server time |
| `-1022` | signature invalid. Since 2026-01-15 this includes signing a payload that was not percent-encoded first |
| `-1100` | illegal characters in a parameter |
| `-1102` | a mandatory parameter was missing or malformed — `symbol` on the per-symbol reads is the usual one |
| `-1104` | not all sent parameters were read — you sent something the endpoint does not take |
| `-2010` | order rejected (balance, or a filter such as tick size / step size / notional) |
| `-2011` | cancel rejected, usually an order that is not on the book |
| `-2013` | order does not exist |
| `-2014` | no usable `X-MBX-APIKEY` arrived. Three causes, cheapest first: the host is not the one your credential is scoped to, so the proxy passed the request through untouched; the path is outside the credential's routes; or this account enrolled the signing secret and never enrolled the api-key credential, which Binance needs on every signed call |
| `-2015` | invalid key, IP or permissions — the key lacks the permission, or Binance has an IP allowlist on it |

## What the key is allowed to do

Binance has no endpoint that reports a key's permissions — unlike some venues,
you cannot ask. What you get instead is a rejection at the point of use:

- **`-2015 Invalid API-key, IP, or permissions for action`** conflates three
  different causes. In order of likelihood: the key lacks the permission for
  that product (futures trading is off by default on a new key), the account
  has an IP allowlist that does not include the proxy's egress address, or the
  key is wrong for the host (a demo key against the live API, or the reverse).
- **`-2014 API-key format invalid`** usually means no key reached the venue at
  all. On a path the proxy covers, that points at the path not being in the
  credential's routes rather than at the key itself.

Say which of the three you think it is and what the user must change on
Binance's API-management page. Do not retry: none of these resolve by
repeating the request.

## Reference

Spot: https://developers.binance.com/docs/binance-spot-api-docs
USD-M futures: https://developers.binance.com/docs/derivatives/usds-margined-futures

Go there for parameter lists, rate limits and the full filter matrix. This file
is the part their docs cannot tell you: that the secret is not yours to hold,
and what to send instead.
