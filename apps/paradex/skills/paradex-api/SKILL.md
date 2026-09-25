---
name: paradex-api
description: >
  Trading and account access on Paradex, the Starknet perpetual futures
  exchange, using an API credential held by the DIME credential proxy. Covers
  the whole flow: turning a stored Starknet signing key into a JWT, reading
  markets, orderbook and account state, placing and cancelling orders, and
  authenticating a WebSocket. Use for ANY request to api.prod.paradex.trade,
  api.testnet.paradex.trade, api.nightly.paradex.trade or the matching
  ws.api.* hosts — including "place an order on Paradex", "what are my Paradex
  positions", "cancel my Paradex orders", "subscribe to Paradex fills", or a
  403 from the credential proxy on a Paradex call. Read this BEFORE calling
  Paradex: it says what to send in place of the secret, which you cannot work
  out from Paradex's own documentation. NOT Paradigm — Paradigm is a separate
  product, an institutional RFQ platform that routes to several venues of which
  Paradex is one. A block trade or an RFQ is paradigm-rfq-trader even when it
  settles on Paradex; this skill is Paradex's own exchange API, direct.
metadata:
  author: tradeparadigm
---

# Paradex

Paradex is a Starknet-based perpetual futures exchange. This skill covers the
calls you will actually make and where the stored credential fits into each
one.

## Paradex is not Paradigm

These trip over each other constantly, so check which one you are being asked
for before going further.

**Paradex** is an exchange. It has an order book, you hold a Paradex credential,
and you call `api.*.paradex.trade` directly. That is this skill.

**Paradigm** is a different product — an institutional RFQ platform that
negotiates block trades and routes them to a venue for settlement. Paradex is
one of the venues it can route to, which is the whole source of the confusion.
It has its own API and its own credentials, and it is covered by the
`paradigm-rfq-trader` skill.

If the request mentions an RFQ, a block trade, quoting, or crossing a quote,
it is Paradigm — **even when the venue is Paradex**. Stop and use that skill
instead. If it is an order on the book, a position, a fill, or market data, it
is Paradex, and you are in the right place.

Your `AGENTS.md` already explains the placeholder mechanism in general — how
`CRED_<NAME>` and `CRED_<NAME>_META` work, what `cred-` and `sign-` prefixes
mean, and how to name the `X-Dime-Sign-` header. This file assumes that and
covers only what is specific to Paradex.

## Hosts

| Environment | REST | WebSocket |
|---|---|---|
| Mainnet | `https://api.prod.paradex.trade/v1` | `wss://ws.api.prod.paradex.trade/v1` |
| Testnet | `https://api.testnet.paradex.trade/v1` | `wss://ws.api.testnet.paradex.trade/v1` |
| Nightly | `https://api.nightly.paradex.trade/v1` | `wss://ws.api.nightly.paradex.trade/v1` |

Check which one the credential is scoped to before assuming mainnet.

## Which credential you hold, and what it can do

An account holds **one** of these per environment, never both. Read the
placeholder's prefix to tell them apart.

### Read-only token — placeholder starts with `cred-`

A Paradex account token. Write the placeholder where the bearer token goes:

```http
Authorization: Bearer cred-paradex-testnet-jwt-8Kq2mQx9vBn4rTz8
```

You can read everything the token's scope allows — account, balance, positions,
fills, order history. **You cannot place orders**, because an order needs a
STARK signature and this credential has no key to produce one.

`CRED_..._META` carries `account`, the account address.

One surprise worth knowing: this credential is scoped to the whole host with no
path narrowing, which means **every** request to that host expects the
placeholder, including public market-data endpoints that do not need
authentication. Write it on all of them. A request that matches the credential's
scope without carrying the placeholder is refused by the proxy before it
reaches Paradex.

### Signing key — placeholder starts with `sign-`

A Starknet private key. You cannot read it, and the proxy signs on your behalf.
This is the credential that can trade.

`CRED_..._META` carries `account` (the account address) and `public_key`. You
need both — neither is derivable from the other on your side, and the auth URL
is addressed to the public key.

It is scoped to exactly three endpoints:

- `POST /v1/auth/*`
- `POST /v1/onboarding`
- `POST /v1/orders`

Everything else on the host is forwarded untouched and needs no placeholder.
That includes all market data, all account reads, and cancelling orders.

## 1. Authenticate: turn the signing key into a JWT

This is the first thing to do with a signing credential, and after it almost
everything else is an ordinary bearer-token request.

**Get the chain id first.** Public, no credential:

```
GET /v1/system/config
```

Read `starknet_chain_id` from the response.

**Build the SNIP-12 message.** SNIP-12 is Starknet's typed-data standard, the
equivalent of EIP-712. The auth message is:

```json
{
  "domain": { "name": "Paradex", "chainId": "<starknet_chain_id as hex>", "version": "1" },
  "primaryType": "Request",
  "types": {
    "StarkNetDomain": [
      { "name": "name", "type": "felt" },
      { "name": "chainId", "type": "felt" },
      { "name": "version", "type": "felt" }
    ],
    "Request": [
      { "name": "method", "type": "felt" },
      { "name": "path", "type": "felt" },
      { "name": "body", "type": "felt" },
      { "name": "timestamp", "type": "felt" },
      { "name": "expiration", "type": "felt" }
    ]
  },
  "message": {
    "method": "POST",
    "path": "/v1/auth",
    "body": "",
    "timestamp": <seconds since epoch>,
    "expiration": <timestamp + a short window, e.g. 1800>
  }
}
```

**`path` is the literal string `/v1/auth`**, even though you POST to
`/v1/auth/<public_key>`. Signing the URL you are actually calling produces a
signature Paradex rejects, and this is the single most common way this call
fails.

**Hash it, and have the proxy sign the hash.** Compute the SNIP-12 message hash
— that needs no secret, so it is your job — and serialise it as exactly 32
bytes. Base64 those bytes into the sign header.

## You cannot hash this by hand

SNIP-12 is Pedersen hashing over encoded felts, and working it out from the
specification costs an agent several minutes and usually still comes out
wrong. Install `starknet` once and let it do the hash:

```sh
cd ~/.openclaw/workspace && npm install --silent starknet@6
```

**A felt is smaller than 32 bytes.** `getMessageHash` returns a hex string
that is routinely 61 or 63 characters, not 64, because a Starknet field
element is under 2^252. Feeding that to `Buffer.from(hex, 'hex')` on an odd
length silently drops a nibble and the proxy sees the wrong payload. Pad it
left to 64 first — `hash.slice(2).padStart(64, '0')` — every time.

Write the helper once and call it for every signed request. The version in the
name is the skill version it came from; if only an older one is there, this
file changed:

```sh
mkdir -p ~/.openclaw/workspace/tools/paradex
rm -f ~/.openclaw/workspace/tools/paradex/paradex-*.mjs
cat > ~/.openclaw/workspace/tools/paradex/paradex-1.1.0.mjs <<'EOF'
import { typedData as td, shortString } from 'starknet';

const V = Object.keys(process.env).find(k => (process.env[k] || '').startsWith('sign-paradex'));
if (!V) { console.error('no Paradex sign- credential in the environment'); process.exit(2); }
const META = JSON.parse(process.env[V + '_META'] || '{}');
const ACCOUNT = process.env.ACCOUNT || META.account;
const PUBKEY = process.env.PUBKEY || META.public_key;
if (!ACCOUNT || !PUBKEY) { console.error('need account and public_key: ' + JSON.stringify(META)); process.exit(2); }

const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');
const HOST = process.env.HOST
  || (/NIGHTLY/i.test(V) ? 'api.nightly.paradex.trade'
  : /TESTNET/i.test(V) ? 'api.testnet.paradex.trade'
  : 'api.prod.paradex.trade');

const cfg = await (await fetch(`https://${HOST}/v1/system/config`)).json();
const chainId = shortString.encodeShortString(cfg.starknet_chain_id);
const DOMAIN = { name: 'Paradex', chainId, version: '1' };
const SND = [{ name: 'name', type: 'felt' }, { name: 'chainId', type: 'felt' }, { name: 'version', type: 'felt' }];

// The only correct way to turn a felt hash into the 32 bytes the proxy wants.
export const payload = (hash) =>
  Buffer.from(hash.slice(2).padStart(64, '0'), 'hex').toString('base64');

export async function auth() {
  const now = Math.floor(Date.now() / 1000), exp = now + 1800;
  const msg = {
    domain: DOMAIN, primaryType: 'Request',
    types: { StarkNetDomain: SND, Request: [
      { name: 'method', type: 'felt' }, { name: 'path', type: 'felt' },
      { name: 'body', type: 'felt' }, { name: 'timestamp', type: 'felt' },
      { name: 'expiration', type: 'felt' }] },
    // The literal string /v1/auth, NOT the URL you are about to call.
    message: { method: 'POST', path: '/v1/auth', body: '', timestamp: now, expiration: exp },
  };
  const res = await fetch(`https://${HOST}/v1/auth/${PUBKEY}`, {
    method: 'POST',
    headers: {
      'PARADEX-STARKNET-ACCOUNT': ACCOUNT,
      'PARADEX-TIMESTAMP': String(now),
      'PARADEX-SIGNATURE-EXPIRATION': String(exp),
      [HDR]: payload(td.getMessageHash(msg, ACCOUNT)),
      'PARADEX-STARKNET-SIGNATURE': process.env[V],
    },
  });
  if (!res.ok) { console.error('auth ' + res.status + ' ' + await res.text()); process.exit(1); }
  return (await res.json()).jwt_token;
}

// size and price are decimal STRINGS as the body carries them; the signed
// message takes the same numbers scaled by 1e8, and side is 1/2 rather than
// BUY/SELL. Two encodings of one field, and getting them out of step is the
// usual cause of a rejected order signature.
export function orderSig({ market, side, size, price, timestamp }) {
  const msg = {
    domain: DOMAIN, primaryType: 'Order',
    types: { StarkNetDomain: SND, Order: [
      { name: 'timestamp', type: 'felt' }, { name: 'market', type: 'felt' },
      { name: 'side', type: 'felt' }, { name: 'orderType', type: 'felt' },
      { name: 'size', type: 'felt' }, { name: 'price', type: 'felt' }] },
    message: {
      timestamp, market, side: side === 'BUY' ? '1' : '2', orderType: 'LIMIT',
      size: String(Math.round(Number(size) * 1e8)),
      price: String(Math.round(Number(price) * 1e8)),
    },
  };
  return payload(td.getMessageHash(msg, ACCOUNT));
}

export { HOST, HDR, ACCOUNT, V };
EOF
```

Reading is then one call:

```sh
node --input-type=module -e "
import { auth, HOST } from '$HOME/.openclaw/workspace/tools/paradex/paradex-1.1.0.mjs';
const jwt = await auth();
const r = await fetch(\`https://\${HOST}/v1/account\`, { headers: { Authorization: 'Bearer ' + jwt } });
console.log(r.status, await r.text());
"
```

**When this block and reality disagree, reality wins.** Change the smallest
thing that makes it work, run it, and say in one line what you changed.

```http
POST /v1/auth/0x4f3c... HTTP/1.1
Host: api.testnet.paradex.trade
PARADEX-STARKNET-ACCOUNT: 0x1a2b...
PARADEX-TIMESTAMP: 1758153600
PARADEX-SIGNATURE-EXPIRATION: 1758155400
X-Dime-Sign-paradex-testnet-priv-key: <base64 of the 32-byte message hash>
PARADEX-STARKNET-SIGNATURE: sign-paradex-testnet-priv-key-7Kj2mQx9vBn4rTz8
```

The proxy replaces the placeholder with the signature as a JSON array of two
decimal strings, `["<r>","<s>"]`, and strips the `X-Dime-Sign-` header so
Paradex never sees what you asked to be signed.

The `PARADEX-TIMESTAMP` and `PARADEX-SIGNATURE-EXPIRATION` headers must carry
the same values you put in the signed message. A mismatch is a rejection with no
useful explanation.

**Keep the token.** The response carries `jwt_token`. Use it as
`Authorization: Bearer <jwt_token>` on everything below. Re-auth when it
expires rather than re-signing every request — the signing credential is scoped
to three endpoints and the token is what reaches the rest of the API.

If the account has never traded, `POST /v1/onboarding` comes first. It uses the
same signing pattern and is idempotent, so calling it when already onboarded is
harmless.

## 2. Market data

All public. No JWT, and no placeholder unless you hold the read-only token
credential (see its surprise above).

| Call | Returns |
|---|---|
| `GET /v1/markets` | Every instrument and its parameters |
| `GET /v1/markets/summary?market=ETH-USD-PERP` | Mark price, 24h volume, open interest |
| `GET /v1/bbo/{market}` | Best bid and offer |
| `GET /v1/orderbook/{market}` | Order book depth |
| `GET /v1/markets/klines` | Candles |
| `GET /v1/trades?market=ETH-USD-PERP` | Recent public trades |
| `GET /v1/funding/data` | Funding rate history |

Market symbols look like `ETH-USD-PERP`. Get exact names from `/v1/markets`
rather than constructing them.

## 3. Account data

Bearer JWT. No signature, no placeholder with a signing credential — these
endpoints are outside its scope and pass straight through.

| Call | Returns |
|---|---|
| `GET /v1/account` | Account value, margin, leverage |
| `GET /v1/balance` | Token balances |
| `GET /v1/positions` | Open positions |
| `GET /v1/orders` | Open orders |
| `GET /v1/orders-history` | Past orders |
| `GET /v1/fills` | Executions |
| `GET /v1/funding/payments` | Funding paid and received |

## 4. Place an order

An order needs **two** credentials' worth of material in one request: the JWT
you minted in step 1, and a fresh STARK signature over the order itself.

**Build the order message** and sign it the same way as auth — SNIP-12, hash to
32 bytes, base64 into the sign header:

```json
{
  "domain": { "name": "Paradex", "chainId": "<starknet_chain_id as hex>", "version": "1" },
  "primaryType": "Order",
  "types": {
    "StarkNetDomain": [ ... as above ... ],
    "Order": [
      { "name": "timestamp", "type": "felt" },
      { "name": "market", "type": "felt" },
      { "name": "side", "type": "felt" },
      { "name": "orderType", "type": "felt" },
      { "name": "size", "type": "felt" },
      { "name": "price", "type": "felt" }
    ]
  },
  "message": {
    "timestamp": <milliseconds since epoch, also the nonce>,
    "market": "ETH-USD-PERP",
    "side": "1",
    "orderType": "LIMIT",
    "size": "<size scaled to 8 decimals>",
    "price": "<price scaled to 8 decimals, or 0 for a market order>"
  }
}
```

- `side` is `1` for BUY and `2` for SELL **in the signed message**, while the
  JSON body below uses the strings `BUY` and `SELL`. They are genuinely
  different encodings of the same field.
- `size` and `price` are quantum values with 8 decimals — multiply by 1e8 and
  take the integer.
- `timestamp` is milliseconds and doubles as the nonce.

**Then send it:**

```http
POST /v1/orders HTTP/1.1
Host: api.testnet.paradex.trade
Authorization: Bearer <jwt_token>
Content-Type: application/json
X-Dime-Sign-paradex-testnet-priv-key: <base64 of the 32-byte order hash>

{
  "market": "ETH-USD-PERP",
  "side": "BUY",
  "type": "LIMIT",
  "size": "0.1",
  "price": "3000",
  "instruction": "GTC",
  "client_id": "my-order-1",
  "signature": "sign-paradex-testnet-priv-key-7Kj2mQx9vBn4rTz8",
  "signature_timestamp": 1758153600000
}
```

`signature_timestamp` **must equal** the `timestamp` you put in the signed
message. The proxy substitutes the placeholder in the body — it scans the JSON
body as well as headers for this credential — so the `signature` field arrives
at Paradex as a real `["<r>","<s>"]` pair.

Order types: `MARKET`, `LIMIT`, `STOP_LIMIT`, `STOP_MARKET`,
`TAKE_PROFIT_LIMIT`, `TAKE_PROFIT_MARKET`, `STOP_LOSS_LIMIT`,
`STOP_LOSS_MARKET`. `price` is omitted for market orders. `flags` takes
`["REDUCE_ONLY"]` when closing.

## 5. Cancel an order

No signature. Bearer JWT alone, and the request passes straight through the
proxy untouched.

```http
DELETE /v1/orders/{order_id}
DELETE /v1/orders/by_client_id/{client_id}
DELETE /v1/orders
```

The last one cancels everything; narrow it with a `market` query parameter.

## 6. WebSocket

You cannot put a per-frame signature on a WebSocket connection — but you do not
need to. **Authentication is a JWT on the first frame, and you can mint that JWT
with the signing key** using step 1. So a signing credential gives you full
WebSocket access.

Connect to `wss://ws.api.{env}.paradex.trade/v1`, then send JSON-RPC 2.0:

```json
{"jsonrpc": "2.0", "id": 1, "method": "auth", "params": {"bearer": "<jwt_token>"}}
```

Send this **before** subscribing to any private channel. Then:

```json
{"jsonrpc": "2.0", "id": 2, "method": "subscribe", "params": {"channel": "bbo.ETH-USD-PERP"}}
```

Channels:

| Channel | Scope |
|---|---|
| `bbo.{market}` | Public |
| `trades.{market}` | Public |
| `markets_summary.{market}` | Public |
| `funding_data.{market}` | Public |
| `order_book.{market}.{feed_type}@15@{refresh_rate}@{price_tick}` | Public |
| `orders.{market}` | Private |
| `fills.{market}` | Private |
| `positions` | Private |
| `account` | Private |
| `balance_events` | Private |
| `transfers`, `tradebusts` | Private |

Subscribing to the same channel twice is an error. Errors come back as JSON-RPC
error objects; codes from -32768 to -32000 are the standard JSON-RPC set.

## When the proxy refuses

A `403` from the DIME proxy is not a Paradex error — Paradex never saw the
request. The body says what was missing. The usual causes here:

- The placeholder absent from a request to one of the three scoped endpoints.
- The `X-Dime-Sign-` header missing, or named from the placeholder instead of
  from the `CRED_<NAME>` variable.
- A sign payload that is not exactly 32 bytes, which means you sent something
  other than a SNIP-12 message hash.

Read the body and correct the request. Do not retry it unchanged.

## Anything not covered here

Full API reference: **https://docs.paradex.trade** — REST under `/api`,
WebSocket under `/ws`. The official Python SDK at
https://github.com/tradeparadex/paradex-py is the authoritative source for
typed-data construction if a signature is being rejected and you cannot see why.

This file is more specific than those docs in one way only: it says where the
credential comes from and what you write instead of it. For anything about
Paradex's own behaviour — instruction types, margin rules, fee tiers, the full
channel list — the docs are correct and this file may lag.
