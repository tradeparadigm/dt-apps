---
name: bybit-api
description: >
  Trading and account access on Bybit's v5 unified API using an API secret held
  by the DIME credential proxy. Covers the whole flow: building Bybit's signed
  string, reading instruments, tickers and orderbook, reading wallet balance and
  positions, placing, amending and cancelling orders, and what to do about
  private WebSocket streams. Use for ANY request to api.bybit.com,
  api-testnet.bybit.com or api-demo.bybit.com — including "buy BTC on Bybit",
  "what is my Bybit balance", "cancel my Bybit orders", "show my Bybit
  positions", or a 403 from the credential proxy on a Bybit call. Read this
  BEFORE calling Bybit: Bybit's signature covers the API key and a byte-exact
  copy of the query string or body, and the order of those bytes is not
  something you can work out from Bybit's own documentation.
metadata:
  author: tradeparadigm
---

# Bybit

Bybit is a centralised exchange. One API — v5 — serves spot, USDT and inverse
perpetuals, futures and options; which one you are trading is a `category`
parameter rather than a different endpoint.

Your `AGENTS.md` already explains the placeholder mechanism in general — how
`CRED_<NAME>` and `CRED_<NAME>_META` work, what the `sign-` prefix means, and
how to name the `X-Dime-Sign-` header. This file assumes that and covers only
what is specific to Bybit.

## Hosts

| Environment | REST base |
|---|---|
| Mainnet | `https://api.bybit.com` |
| Testnet | `https://api-testnet.bybit.com` |
| Demo trading | `https://api-demo.bybit.com` |

Check which one your credential is scoped to before assuming mainnet. Testnet
and demo trading are different things: testnet is a separate chain with fake
markets, demo trading is mainnet's real market data with a simulated balance.

## What you hold

One credential, and one piece of public metadata that comes with it.

- **`CRED_BYBIT_<ENV>_SECRET`** — the API secret, as a `sign-` placeholder. You
  never see the secret. You put the placeholder where the signature goes and
  tell the proxy what to sign.
- **`CRED_BYBIT_<ENV>_SECRET_META`** — JSON, containing `api_key`. This is your
  API key. It is **not** a secret in the sense the API secret is: it identifies
  the key pair and cannot authenticate anything on its own. You send it in a
  header yourself.

**Send the api key verbatim.** It goes in `X-BAPI-API-KEY` and it is also the
second field of the signed string, so it has to be the real value in both. The
habit of masking a credential in a command you are about to show someone —
`***`, `<redacted>`, `$KEY` left unexpanded — breaks the call here, and Bybit
reports it as a signature problem rather than a key problem. Mask it when you
QUOTE the command afterwards, never in the request.

The exact variable names depend on the label chosen at enrolment; read them
from the environment rather than assuming.

## Know what the key can do, before you need it

```
GET /v5/user/query-api    # signed
```

One call, and it answers the questions that otherwise surface as a confusing
rejection halfway through a task:

- `permissions` — per product group: `ContractTrade`, `Spot`, `Options`,
  `Derivatives`, `Wallet`, and more. An empty list means the key cannot touch
  that product at all. A key with `Spot: []` will fail every spot call no
  matter how well formed.
- `readOnly` — `0` means it can trade, `1` means every order will be refused.
- `expiredAt` and `deadlineDay` — Bybit keys EXPIRE. `deadlineDay` counts down.
- `ips` — `["*"]` is any address; a list means the request must come from one
  of them, and the proxy's egress address is what Bybit sees, not yours.
- `uta` — whether this is a Unified Trading Account, which decides whether
  `accountType=UNIFIED` is the right read.

**Check this first when a call fails with a permission or auth error**, and say
which permission is missing rather than retrying. The user has to fix that on
Bybit's website; no amount of retrying will.

## Every private request

Four headers, always, and they are coupled — three of them also go into the
string that gets signed.

```
X-BAPI-API-KEY:     <api_key from _META>
X-BAPI-TIMESTAMP:   <milliseconds since epoch>
X-BAPI-RECV-WINDOW: 5000
X-BAPI-SIGN:        <the sign- placeholder>
```

The string to sign is these three values concatenated with the request payload,
with **no separators at all**:

```
timestamp + api_key + recv_window + payload
```

where `payload` is:

- for **GET / DELETE**: the query string exactly as it appears in the URL,
  without the leading `?` — e.g. `category=linear&symbol=BTCUSDT`
- for **POST**: the raw request body, byte for byte, exactly as you will send
  it

**Compute the timestamp ONCE.** The value in `X-BAPI-TIMESTAMP` and the value
you concatenated must be the same variable, and so must `recv_window`. Building
one for the header and another for the signed string is the single most common
way to produce a signature Bybit rejects, and it fails intermittently — the two
agree whenever they land in the same millisecond.

**Byte-exact matters more than anything else on this page.** Bybit recomputes
the HMAC over what it received. If you build the JSON body one way for signing
and let an HTTP library re-serialise it another way when sending — different
key order, added whitespace, a float rendered differently — the signature is
over a string Bybit never sees and you get `10004 error sign!`. Serialise the
body **once**, into a string, sign that string, and send that same string.

### The request

One block. Change the three lines between the markers and run it. Everything
else is the same for every Bybit call you will ever make, so do not rewrite it,
and do not build the request out of `curl` — a JSON body inside shell quoting
cannot survive an apostrophe or a computed value, and that is exactly where
byte-exactness dies.

```sh
node <<'EOF'
(async () => {
  // Picked by VALUE, not by name: an empty CRED_BYBIT can sit beside the real
  // one, and matching on the name alone selects it depending on env order.
  const V = Object.keys(process.env).find(k => (process.env[k] || '').startsWith('sign-bybit'));
  const KEY = JSON.parse(process.env[V + '_META']).api_key;
  const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');
  const HOST = /TESTNET/.test(V) ? 'api-testnet.bybit.com'
             : /DEMO/.test(V)    ? 'api-demo.bybit.com'
             :                     'api.bybit.com';

  // ---- change these three ----
  const method  = 'GET';
  const path    = '/v5/position/list';
  const payload = 'category=linear&settleCoin=USDT';   // GET: query string. POST: JSON.stringify(...)
  // ----------------------------

  const ts = Date.now().toString();
  const get = method === 'GET';
  const res = await fetch(`https://${HOST}${path}` + (get && payload ? '?' + payload : ''), {
    method,
    headers: {
      'X-BAPI-API-KEY': KEY,
      'X-BAPI-TIMESTAMP': ts,
      'X-BAPI-RECV-WINDOW': '5000',
      'X-BAPI-SIGN': process.env[V],
      [HDR]: Buffer.from(ts + KEY + '5000' + payload).toString('base64'),
      ...(get ? {} : { 'Content-Type': 'application/json' }),
    },
    ...(get ? {} : { body: payload }),
  });
  console.log(res.status, await res.text());
})();
EOF
```

To place an order instead, those three lines become:

```js
const method  = 'POST';
const path    = '/v5/order/create';
const payload = JSON.stringify({ category: 'linear', symbol: 'BTCUSDT', side: 'Buy',
                                 orderType: 'Limit', qty: '0.001', price: '50000',
                                 timeInForce: 'PostOnly' });
```

Why it is shaped this way:

- `payload` is built once and used twice — signed, then sent. Nothing
  re-serialises it in between, which is the failure this whole page is about.
- The credential is read from the environment by the block itself, so the api
  key is never something you type, quote or mask.
- `HDR` comes from the variable name, never from splitting the placeholder —
  see the warning in `AGENTS.md`.
- `res.status` is printed. An empty-bodied 401 and a 200 with an empty result
  are indistinguishable otherwise.

The proxy computes HMAC-SHA256 over your bytes with the secret, writes the hex
digest into `X-BAPI-SIGN` in place of the placeholder, strips the
`X-Dime-Sign-` header, and forwards the request. Bybit sees an ordinary signed
Bybit request.

If you get a 403 from the proxy rather than an error from Bybit, the placeholder
was not where it was expected — the body of that 403 names the header it
scanned.

## Which calls need signing

The proxy demands the placeholder on exactly these paths, and refuses a request
to one of them without it:

```
/v5/order/*      /v5/position/*      /v5/execution/*
/v5/account/*    /v5/asset/*         /v5/user/*
/v5/spot-margin-trade/*
```

Everything under `/v5/market/*` is public. Send those with no auth headers at
all — no api key, no timestamp, no signature. Adding a signature to a public
call is not harmless here: the proxy is not watching those paths, so your
placeholder would go to Bybit verbatim.

**There is a third kind, and it is the one that will waste your time.** Bybit
has authenticated families outside that list — `/v5/spot-lever-token/*` is one
— and this credential CANNOT sign them. The proxy substitutes only on a path
one of its rules matches, so on any other path your placeholder travels to
Bybit as literal text and Bybit rejects it. Your signing was fine. Re-deriving
it will not help, and neither will retrying.

Check the path against the list above BEFORE you conclude anything about your
signature. If the call is one the user needs, say so plainly: the credential's
allowed routes have to be widened where it was enrolled, which is their action
and not something you can work around.

## Market data (public, unsigned)

```
GET /v5/market/instruments-info?category=linear&symbol=BTCUSDT
GET /v5/market/tickers?category=linear&symbol=BTCUSDT
GET /v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=25
GET /v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=200
```

`category` is required on nearly every v5 call: `spot`, `linear`, `inverse`
(coin-margined) or `option`.

### What lives under `linear`, and how to tell them apart

`linear` is three different products sharing one category — 885 contracts
today:

| Settles | Contract | Count | Symbol looks like |
|---|---|---|---|
| USDT | `LinearPerpetual` | 777 | `BTCUSDT`, `ETHUSDT` |
| **USDC** | `LinearPerpetual` | 68 | **`BTCPERP`**, `ETHPERP`, `1000PEPEPERP` |
| USDT | `LinearFutures` | 40 | `BTCUSDT-25DEC26` (dated) |

**A USDC perpetual is `BTCPERP`, not `BTCUSDC`.** That is the single most
common way to ask Bybit for a contract that does not exist. Dated futures carry
the expiry after a dash, and every one of them settles USDT — there are no USDC
futures listed.

Filter with `settleCoin` (`USDT` or `USDC`) and read `contractType` to tell a
perpetual from a dated future. `settleCoin` is also what most signed queries
want: `position/list?category=linear&settleCoin=USDC` returns the USDC book and
nothing else.

`inverse` is separate again: coin-margined, settling in the base asset
(`BTCUSD` settles BTC), 28 contracts.

**USDC contracts have session settlement and USDT ones do not.** Unrealised PnL
on a USDC position is periodically realised, and the record is
`GET /v5/asset/settlement-record` — `category=linear` and USDC only. A user
asking "why did my PnL move without a trade" on a `*PERP` symbol is asking
about this. There is no equivalent for USDT contracts, and none for options.

Check `instruments-info` before placing your first order on a symbol. It carries
`lotSizeFilter.qtyStep`, `priceFilter.tickSize` and
`lotSizeFilter.minNotionalValue` (5 USDT/USDC on BTC either way), and an order
that is not a multiple of those, or is below the minimum notional, is
rejected.

## Spot

`category=spot`, and its instrument shape is not the derivatives one.

```
GET /v5/market/instruments-info?category=spot&symbol=BTCUSDT
```

`lotSizeFilter` carries `basePrecision`, `quotePrecision`, `minOrderQty`,
`minOrderAmt` (5 USDT on BTCUSDT) and separate `maxLimitOrderQty` /
`maxMarketOrderQty` — there is no `qtyStep`, and `priceFilter` is `tickSize`
alone. An order can satisfy the quantity precision and still be refused for
falling under `minOrderAmt`.

Spot reads take no `settleCoin` — it is a derivatives concept. Use `symbol`.

Spot trading is a SEPARATE permission on the key (`Spot: ["SpotTrade"]`); a key
that trades perpetuals happily may have nothing for spot. See the key-scope
section above before concluding a spot failure is your request's fault.

## Options

`category=option`, and almost nothing above transfers unchanged. Options trade
only under the Unified Trading Account.

### Symbols and what is listed

`BASE-EXPIRY-STRIKE-C|P-QUOTE` — `BTC-25SEP26-130000-C-USDT`,
`ETH-26MAR27-3700-P-USDT`, `SOL-24SEP26-111-C-USDT`. The day has no leading
zero (`6NOV26`). Every listed contract settles **USDT**; there is no USDC
suffix in the live catalogue, so never construct one.

```
GET /v5/market/option-base-coins                                  # what is tradable
GET /v5/market/instruments-info?category=option&baseCoin=BTC      # baseCoin=All for every series
GET /v5/market/tickers?category=option&baseCoin=BTC
```

**`baseCoin` is REQUIRED on the market endpoints** — `tickers?category=option`
alone is `10001 PARAMS_ERROR`. The SIGNED endpoints are the opposite:
`category=option` on its own is accepted, where `linear` demands a symbol or
settle coin.

`instruments-info` for an option returns `priceFilter` (`minPrice`, `maxPrice`,
`tickSize`) and `lotSizeFilter` (`minOrderQty`, `maxOrderQty`, `qtyStep`) and
**nothing else** — no `minNotionalValue`, no `leverageFilter`. BTC options quote
in whole units of 5 USDT (`minPrice: 5`, `tickSize: 5`) with `qtyStep: 0.01`
contracts. Check the filters before every first order on a symbol; a price that
is not a multiple of `tickSize` is rejected.

It also defaults to returning `PreLaunch`, `Trading` AND `Delivering` symbols,
unlike the other categories. Filter on `status == "Trading"` before offering
something as tradable.

### Placing an option order

```
POST /v5/order/create
{"category":"option","symbol":"BTC-25DEC26-350000-C-USDT","side":"Buy",
 "orderType":"Limit","qty":"0.01","price":"5","timeInForce":"GTC",
 "orderLinkId":"my-order-1"}
```

- **`orderLinkId` is REQUIRED for options.** It is optional everywhere else on
  this venue, and omitting it here is rejected.
- `qty` is contracts, never a quote amount.
- **`orderIv` is option-only** — pass implied volatility as a real number
  (`0.1` for 10%). If you send both, `orderIv` wins over `price`.
- **`mmp` is option-only** (market-maker protection orders).
- **Conditional and trigger orders are not available**: `triggerPrice`,
  `triggerBy`, `triggerDirection`, `closeOnTrigger`, `tpslMode` and the
  partial/limit TP-SL fields are documented for `linear` and `inverse` only.
  An option take-profit or stop-loss is full-size and market-type.
- `reduceOnly` does work for options.

Two limits that bite market-makers rather than one-shot traders: **50 open
orders per coin** (not per symbol), and `/v5/order/cancel-all` is rate-limited
to **1/s** for options against 10/s elsewhere.

### Greeks, and where they come from

Do not compute your own. Three layers, all live:

```
GET /v5/market/tickers?category=option&baseCoin=BTC   # per symbol: delta gamma vega theta
                                                      # markIv bid1Iv ask1Iv underlyingPrice
GET /v5/asset/coin-greeks?baseCoin=BTC                # account totals per base coin
GET /v5/account/option-asset-info?baseCoin=BTC        # option-only: totalDelta, UPL/RPL, IM/MM
```

`/v5/account/coin-greeks` does **not** exist — it is a 404. The greeks live
under `/v5/asset/`.

`/v5/position/list?category=option` carries `delta`, `gamma`, `vega`, `theta`
too, and two fields that are empty for options and mean nothing:
`cumRealisedPnl` and `breakEvenPrice`. **`/v5/position/closed-pnl` has no
`option` category at all** — do not call it for options.

### Expiry

```
GET /v5/market/delivery-price?category=option&symbol=...
GET /v5/asset/delivery-record?category=option           # strike, deliveryRpl
```

Without a symbol, `delivery-price` returns only contracts currently DELIVERING
(08:00–12:00 UTC). After expiry the outcome shows up in `delivery-record` with
the strike and the realised PnL of the delivery.

### Market maker protection

`POST /v5/account/mmp-modify`, `POST /v5/account/mmp-reset`,
`GET /v5/account/mmp-state` — all option-only, all keyed by `baseCoin` with no
`category`. MMP has to be enabled by Bybit for the account first; it is not
self-service, so `mmp-state` answering is not the same as MMP being on.

## Account state (signed)

```
GET /v5/account/wallet-balance?accountType=UNIFIED
GET /v5/position/list?category=linear&settleCoin=USDT
GET /v5/order/realtime?category=linear&settleCoin=USDT   # working orders
GET /v5/order/history?category=linear&limit=50          # closed orders
GET /v5/execution/list?category=linear&limit=50         # fills
```

**`order/realtime` needs more than `category` on `linear`**: one of `symbol`,
`settleCoin` or `baseCoin` is required, and without it Bybit answers
`10001 params error`. `inverse` does not — `category=inverse` alone returns the
list. Neither do `order/history` and `execution/list`.

`accountType` is `UNIFIED` for a Unified Trading Account, which is what a
recent Bybit account is. If that returns nothing, try `CONTRACT` (legacy
derivatives) or `SPOT`.

## Placing an order

```
POST /v5/order/create
Content-Type: application/json
```

```json
{
  "category": "linear",
  "symbol": "BTCUSDT",
  "side": "Buy",
  "orderType": "Limit",
  "qty": "0.001",
  "price": "60000",
  "timeInForce": "GTC",
  "orderLinkId": "my-order-1"
}
```

Sign the body string exactly as you will send it — see the warning above.

- `side` is `Buy` or `Sell`, capitalised exactly like that.
- `orderType` is `Limit` or `Market`. A market order omits `price`.
- `qty` and `price` are **strings**, not numbers. Sending them as JSON numbers
  is a common source of both rejections and signature mismatches.
- `timeInForce`: `GTC`, `IOC`, `FOK`, or `PostOnly` for a maker-only order.
- `orderLinkId` is your own id, up to 36 characters. Set it. It is how you
  cancel or amend without first looking up Bybit's `orderId`, and how a retry
  after a timeout avoids placing the order twice — Bybit rejects a duplicate
  `orderLinkId`.
- `reduceOnly: true` to close rather than open.

A `200` with `retCode: 0` means accepted. **Any other `retCode` is a failure
even though the HTTP status is 200** — always read `retCode` and `retMsg`
rather than trusting the status line.

## Amending and cancelling

```
POST /v5/order/amend     {"category":"linear","symbol":"BTCUSDT","orderLinkId":"my-order-1","price":"59000"}
POST /v5/order/cancel    {"category":"linear","symbol":"BTCUSDT","orderLinkId":"my-order-1"}
POST /v5/order/cancel-all {"category":"linear","settleCoin":"USDT"}
```

All three are `POST`, including cancel — Bybit has no `DELETE` verb on orders.
Identify an order by `orderId` or `orderLinkId`; one of the two is required.

`cancel-all` without a `symbol` cancels every open order in the category, so
scope it with `symbol` or `settleCoin` unless that is genuinely what was asked
for.

## WebSocket

**Public streams work.** `wss://stream.bybit.com/v5/public/linear` and its
siblings need no authentication — subscribe and read.

**Private streams do not work through the credential proxy.** Bybit
authenticates `wss://stream.bybit.com/v5/private` with an `auth` frame sent
*after* the connection is established, carrying a signature over
`"GET/realtime" + expires`. The proxy signs HTTP requests; it does not sign
WebSocket frames, and there is no placeholder mechanism inside a frame.

So for order updates, fills and position changes, poll the signed REST
endpoints above instead — `/v5/order/realtime` for working orders,
`/v5/execution/list` for fills. Say so if asked to stream private data: it is a
real limitation, not something to work around by asking for the secret.

## When something fails

- **403 from the credential proxy**, body naming `placeholder_absent` — you
  called a signed path without putting the placeholder in `X-BAPI-SIGN`. The
  body lists where it looked.
- **`retCode: 10004`, `error sign!`** — the string you signed is not the string
  Bybit reconstructed. Almost always the body was re-serialised after signing,
  or the query string order changed, or `recv_window` in the header differs from
  the one you concatenated. **Check the path first**: on a private family
  outside the signed list — `/v5/spot-lever-token/*` and friends — the proxy
  never substituted at all and Bybit is comparing against the literal
  placeholder. Nothing about your signing is wrong and no amount of redoing it
  will help.
- **`retCode: 10002`, `invalid request, please check your timestamp`** — your
  timestamp is outside `recv_window`. Use milliseconds, not seconds.
- **`retCode: 10003`, `API key is invalid`** — the `api_key` in `_META` does not
  match the enrolled secret, or the key was revoked on Bybit's side, or you sent
  a masked value in `X-BAPI-API-KEY`. Print the header you actually sent before
  you start re-deriving the signature.
- **`retCode: 110043`, leverage not modified** and similar — Bybit returns many
  non-zero `retCode`s that are informational. Read `retMsg` before treating one
  as fatal.

**Tell the two layers apart by the SHAPE of the failure, not by guessing.**

- **HTTP 403 with a plain-text body** is the credential proxy, and that body is
  the answer: it names the placeholder to use and lists exactly where it looked
  for it. Read it and retry. The request never reached Bybit.
- **HTTP 401 with an EMPTY body**, `server: Openresty`, CloudFront headers, is
  BYBIT rejecting the request at its edge before it writes a JSON body. Either
  the signed bytes do not match what you sent, or `X-BAPI-API-KEY` is not a real
  key. The same bad key answers 10003 on some endpoints and an empty 401 on
  others, so check the key header before you re-derive the signature; after
  that, the timestamp discipline above.
- **HTTP 200 with a non-zero `retCode`** is Bybit's application layer — the
  request authenticated and the venue disagreed with its contents.

Do not read an empty-bodied 401 as a proxy refusal. A proxy refusal always has
a body that tells you what to do.

## Reference

Full API documentation: https://bybit-exchange.github.io/docs/v5/intro

Go there for anything this file does not cover — parameter lists, rate limits,
error codes, the full `category` matrix. This file is the part Bybit's own docs
cannot tell you: that the secret is not yours to hold, and what to send instead.
