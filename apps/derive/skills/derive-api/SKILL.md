---
name: derive-api
description: >
  Account access on Derive (formerly Lyra), the onchain options and perps
  exchange, over its v3 API at api.derive.xyz, using an Ethereum wallet key
  held by the DIME credential proxy: subaccounts, positions and their greeks,
  open and trigger orders, collateral balances and market data. Ships the
  client that signs every call. Use for ANY Derive request — "what are my
  Derive positions", "my Derive greeks", "my Derive balance", "Derive ETH
  option prices" — and for any call to api.derive.xyz or the retired
  api.lyra.finance, including a 403 from the credential proxy or a 14014 from
  Derive. Read this BEFORE calling Derive: its errors arrive as HTTP 200, and
  three position fields do not mean what their names say.
metadata:
  author: tradeparadigm
---

# Derive

Onchain options, perpetuals and spot. You do not have the key: it is sealed in
the credential proxy, and the client beside this file asks the proxy to sign
each call.

**Derive answers HTTP 200 when it refuses you.** It is JSON-RPC: data is under
`result`, a failure is an `error` object with `code`, `message` and `data`,
and the status is 200 either way. Read the body, never only the status. An
auth failure read as data looks like an empty account.

## Hosts

| Environment | Base |
|---|---|
| Mainnet | `https://api.derive.xyz/v3` |
| Testnet | `https://testnet.api.derive.xyz/v3` |

`/v3` is part of the base, not optional: `/public/get_time` without it is a
404. Every call is `POST` with a JSON body, reads included.

`api.lyra.finance` and `api-demo.lyra.finance` are retired (503 and 530 on
every path since November 2026), and Derive no longer reads the `X-Lyra*`
headers. Anything that names either — a note, a script, an older copy of this
skill — predates v3.

Earlier versions of this skill had you write a helper under
`~/.openclaw/workspace/tools/derive/`. It calls the retired host. If that
directory exists, delete it:

```sh
rm -rf ~/.openclaw/workspace/tools/derive
```

## What you were given

The credential arrives as `CRED_<NAME>` holding a `sign-…` placeholder, plus
`CRED_<NAME>_META` holding the non-secret facts:

| Key | What it is |
|---|---|
| `owner` | The wallet that owns the account on Derive |
| `signer` | The address whose key signs. The owner's, unless a session key was registered |
| `subaccount_id` | A subaccount the user named. Optional, and may be blank |

## Every call goes through the client

Run `scripts/derive.mjs`, beside this file. It needs nothing installed.

```sh
node scripts/derive.mjs subaccounts

TARGET=/private/get_positions BODY='{"subaccount_id": 73340}' node scripts/derive.mjs

TARGET=/public/get_ticker BODY='{"instrument_name": "ETH-PERP"}' node scripts/derive.mjs
```

- `TARGET` is the method's path, such as `/private/get_positions`. The client
  adds `/v3`.
- `BODY` is the method's params as JSON. Without it the client sends
  `{"wallet": "<owner>"}`, which is what the wallet-level methods take. Derive
  ignores a field a method does not take.
- It prints `<status> <body>`, and exits 0 on a `result`, 1 when Derive or the
  proxy refused the call, and 2 when it cannot start. On a refusal it also
  prints the proxy's `x-dime-credential-error` header when there is one.
- It signs every call, public ones included. Why is under "Which calls the
  proxy signs".
- The host comes from the credential's name: `MAINNET` or `TESTNET` in it. When
  the name says neither it refuses rather than guess, because one wallet key
  can own accounts on both. Set `DERIVE_HOST=api.derive.xyz` or
  `DERIVE_HOST=testnet.api.derive.xyz`.
- With several Derive credentials it refuses until you name one:
  `DERIVE_CRED=CRED_…`.
- The owner comes from `_META`. `OWNER=0x…` overrides it, and is needed when
  `_META` has none. Ask the user for it then.

Public market data needs no credential. Without one, call Derive directly:

```sh
curl -s -X POST https://api.derive.xyz/v3/public/get_ticker \
  -H 'Content-Type: application/json' -d '{"instrument_name": "ETH-PERP"}'
```

### When the client is wrong

Never edit `scripts/derive.mjs`. A publish replaces it, so an edit there is
lost and the next chat repeats your work. Copy it into
`~/.openclaw/workspace/` under a name of your own, change the smallest thing
that makes the call work, run that, and tell the user in one line what you
changed and why, so the fix can come back here. Do not stop to ask, and do not
re-run the unchanged client to prove it fails.

## Start with the subaccounts

Almost every private method takes a `subaccount_id`, and a wallet may own
several:

```sh
node scripts/derive.mjs subaccounts
```

```
{"wallet":"0x1a2b…","subaccount_ids":[73340,73341],"public":[73340,73341],"private":[73340]}
```

It asks `public/get_subaccounts` and `private/get_subaccounts` and takes the
union. **Use the union.** The two have been seen to disagree for a while on the
same wallet. The subaccount the private one left out held the account's only
position, so reading one endpoint reported an empty book while the exposure
went untracked. Asking both costs one call.

It exits 0 when the private half answered, which also proves the whole signing
path works. Exit 1 with ids from the public half means the ids are real but
signed calls are failing: see "If a call is refused". If both halves fail, the
credential's `subaccount_id` detail is the only fallback, and it may be blank.

A subaccount in the union can still refuse you. `14020` on a subaccount that
`public/get_wallet` says this wallet owns is a permission boundary, not a fault.
It may be a vault, or the session key may cover only some subaccounts. Say so
and read the others; retrying will not change it.

## Reading an account

| What | Method | Records under |
|---|---|---|
| Positions | `private/get_positions` | `positions` |
| Resting orders | `private/get_open_orders` | `orders` |
| Trigger orders | `private/get_trigger_orders` | `orders` |
| Balances | `private/get_collaterals` | `collaterals`: `currency`, and `amount`, negative when borrowed |
| All of one subaccount | `private/get_subaccount` | the portfolio, with all of the above |

Each takes `{"subaccount_id": N}`. Ask every subaccount in the union.

### Three position fields that do not mean what they say

Each produced plausible wrong numbers in a working integration, and none fails
loudly.

- **Greeks are per unit, as if long.** A position's `delta`, `gamma`, `vega`
  and `theta` are for one unit of the underlying held long. They are not
  scaled to the position and not signed by its direction. Multiply each by the
  signed `amount`. A short 0.03 ETH call reports `delta` 0.093 and `theta`
  −1.88; the position's are −0.0028 and +0.056. Summed raw, a book's net delta
  and theta come out inverted.
- **`average_price` is a fee-adjusted basis, not a price.** It is
  `average_price_excl_fees + total_fees / amount`, and `amount` is negative on
  a short, so a short option sold at 6.1 shows `average_price` −11.23. The
  entry price is `average_price_excl_fees`. `mark_price` excludes fees, so
  compare it only with that.
- **`unrealized_pnl` already excludes funding.** Funding is in
  `cumulative_funding` and `pending_funding`, and their sum is the funding
  since the position opened. Do not subtract it again. Paradex includes
  funding in its PnL; Derive does not.

`amount` is signed, negative when short, and a position with `amount` 0 is
closed. `liquidation_price` and `leverage` can be null: report null as "not
given", never as 0, which reads as a real liquidation level.

How each of these was established, instrument names and expiries, and the
rules for anything that reads a whole account are in
[references/records.md](references/records.md). Read it before building on
these records rather than answering one question.

### Market data uses short keys

`public/get_ticker` answers with short keys:

| Key | Meaning |
|---|---|
| `M`, `I` | Mark price, index price |
| `b`, `a` | Best bid price, best ask price |
| `B`, `A` | Best bid amount, best ask amount |
| `f` | Funding rate on a perp; null on an option |
| `minp`, `maxp` | The order price band |
| `t` | Time, in milliseconds |
| `stats` | 24h figures: `v` volume, `oi` open interest, `h`/`l` high/low, `p` change |

An option's pricing is under `option_pricing`, per unit:

| Key | Meaning |
|---|---|
| `d`, `g`, `v`, `t`, `r` | Delta, gamma, vega, theta, rho |
| `i` | Mark IV |
| `bi`, `ai` | Bid IV, ask IV |
| `m` | Mark price |
| `f` | Forward price |

Reading `mark_price` or `option_pricing.delta` gives `undefined`. The key is
wrong; the value is not missing.

Instruments come from `public/get_all_instruments`, for example
`{"currency": "ETH", "instrument_type": "option", "expired": false}`. The
older `public/get_instruments` is a 404 on v3.

## How a call is signed

The client does all of this. Read it when a call fails, or to sign without the
client.

Every private call carries three headers:

| Header | Value |
|---|---|
| `X-DeriveWallet` | the `owner` address |
| `X-DeriveTimestamp` | current time in **milliseconds**, as a decimal string |
| `X-DeriveSignature` | `0x` and the signature over that timestamp string |

The digest is EIP-191 `personal_sign` over the timestamp string — the string,
not the number:

```
msg    = "1790243017651"
digest = keccak256("\x19Ethereum Signed Message:\n" + len(msg) + msg)
```

`len(msg)` is the byte length as a decimal string, so `13` here. The timestamp
you sign and the timestamp you send must be the same one.

**keccak256, not sha3-256.** Node's `crypto` offers `sha3-256`, which is a
DIFFERENT hash: NIST padding, not Keccak padding. It returns a perfectly good
32 bytes, the proxy signs them, and Derive recovers some stranger's address
from the result and answers `14026 Session key not found`, with nothing
pointing at the cause. This is the most expensive mistake on this page. The
client carries its own keccak256. Signing without it, install `ethers` in
`~/.openclaw/workspace` and use its `hashMessage`.

### Asking the proxy to sign

Two things in the same request, and either alone is refused:

1. The 32-byte digest, base64-encoded, in `X-Dime-Sign-<name>`. Take `<name>`
   from the `CRED_<NAME>` variable, lowercased with `_` written as `-`. The
   proxy strips this header before forwarding.
2. The placeholder where the signature belongs, in `X-DeriveSignature`, with
   `0x` written in front of it.

**Send the digest, not the message.** The proxy signs 32 bytes and refuses
anything else. That is Derive's own model: their signing library signs a raw
hash too. The signature comes back as `r||s||v` in hex, 130 characters, with
`v` as 27 or 28.

```http
POST /v3/private/get_account HTTP/1.1
Host: api.derive.xyz
Content-Type: application/json
X-Dime-Sign-derive-mainnet-wallet-key: <base64 of the 32-byte digest>
X-DeriveWallet: 0x1a2b…
X-DeriveTimestamp: 1790243017651
X-DeriveSignature: 0xsign-derive-mainnet-wallet-key-7Kj2mQx9vBn4rTz8wLpAeF

{"wallet": "0x1a2b…"}
```

### Which calls the proxy signs

The credential's endpoint scope decides which paths need the placeholder, and
two are in use:

- **The app's own, `/v3/private/*`.** Public calls pass through untouched.
- **Any endpoint.** Every call to the host needs the `X-Dime-Sign-…` header,
  public ones too, or the proxy refuses it with 403 before it leaves the pod.

The client signs everything, which works under both: Derive ignores the auth
headers on a public route. Code that skips signing public calls fails under the
second scope, and can take a whole fetch down with it.

A scope written `/private/*` matches nothing, because Derive's paths start with
`/v3/`. Then the proxy never signs, and the placeholder reaches Derive as text.
A credential whose host is not the one called does the same. That includes one
enrolled while this app named `api.lyra.finance`. Either way Derive answers
`14014` "Signature not formatted correctly" and there is no 403 to read, so it
looks like a signing bug when it is not one:

```
request → do the credential's host and scope match the call?
  no  → forwarded unchanged     → Derive 14014 "Signature not formatted correctly"
  yes → X-Dime-Sign-<name> header present?
          no  → proxy 403, x-dime-credential-error: placeholder_absent
          yes → key retrievable?
                  no  → proxy 403, x-dime-credential-error: secret_unavailable
                  yes → signed and forwarded → Derive's answer
```

The probe that settles which branch you are on, in one call: repeat the
request without the digest.

```sh
DERIVE_UNSIGNED=1 TARGET=/private/get_subaccounts node scripts/derive.mjs
```

A proxy 403 means the proxy is in the path, so the fault is in what you sent.
The same Derive answer as before means the proxy is not in the path: the host
or scope does not match, and only the user can change that.

## If a call is refused

Each symptom lists every cause known, cheapest to check first. Codes are from
Derive's v3 catalogue (<https://docs.derive.xyz/error-codes.md>). Any code
not here means Derive understood the call and disagreed with it; read its
`message` and `data`.

| What you see | Causes, cheapest first | Who fixes it |
|---|---|---|
| HTTP 503 or 530, or no connection | The host is `api.lyra.finance` or `api-demo.lyra.finance`, which are retired. Use the client. | You |
| HTTP 404 `Not Found` | The path lacks `/v3`. Or the method does not exist on v3, as with `public/get_instruments`; look it up at <https://docs.derive.xyz/llms.txt>. | You |
| HTTP 403, plain text | The credential proxy refused it, and the body says why. `placeholder_absent`: the path is in the credential's scope and the `X-Dime-Sign-…` header was missing or named for another credential, as with a public call sent unsigned under an "any endpoint" scope. `secret_unavailable`: the proxy could not fetch the key. Payload not a digest: you sent the message; hash it. | You; the user for `secret_unavailable` |
| HTTP 403, an HTML page ending `derive` | Derive's own edge refused it before JSON-RPC. Seen when `X-DeriveTimestamp` is far from now: seconds instead of milliseconds, or a day old. | You |
| `-32602` `Missing timestamp in header` or `Missing wallet in header` | That header is missing. Or the request sent the `X-Lyra*` headers, which Derive no longer reads; an old helper does this. | You |
| `-32602` `Invalid params: missing field …` | `BODY` lacks a param the method needs. | You |
| `14014`, data `Signature not formatted correctly` | `X-DeriveSignature` is not 65 bytes of hex. A signature the proxy wrote always is, so the proxy did not sign. Its scope says `/private/*` rather than `/v3/private/*` or any endpoint. Or its host is not the one called, as with a credential enrolled for `api.lyra.finance`. Or the placeholder sits in a header other than `X-DeriveSignature`. Or the value around it is mangled, such as `0x` written twice. Run the probe above. No change to the digest can fix this. | The user re-enrols the credential, or corrects its host and scope; you, for a mangled value |
| `14014`, data `Failed to recover signer from signature` | 65 bytes arrived that no address can be recovered from, such as all zeros or a `v` that is not 27 or 28. That is a hand-built or dummy value, not the proxy's signature. | You |
| `14026` `Session key not found`, data `address: 0x…` | The address is the one Derive recovered from the signature. **Not your `signer`:** the digest was wrong. Perhaps sha3-256 was used instead of keccak256, the timestamp sent is not the one signed, or the EIP-191 prefix is missing. **Your `signer`:** that key is not a session key of the `X-DeriveWallet` wallet. The header may name the wrong owner, or the key was never registered. | You for the digest; the user for the key |
| `14030` `Session key expired` | The signer's session key is past its expiry. | The user registers a new one |
| `14031` `Unauthorized Key Scope` | The session key's scopes do not cover this method. | The user |
| `14000` `Account not found` | `X-DeriveWallet` has no account on this environment. The owner address is wrong, or it is a testnet wallet on mainnet or the reverse. A correct signature from a wallet with no account lands here too, so the signing itself worked. | You, or the user for the address |
| `14001` `Subaccount not found` | That `subaccount_id` does not exist. Take ids from `subaccounts`. | You |
| `14020` | `X-DeriveWallet` does not match the subaccount or wallet in the body. The `subaccount_id` is wrong, or `wallet` in `BODY` is not the owner. Or the wallet owns the subaccount but this signer may not act on it, as with a vault or a session key limited to other subaccounts. Do not retry. | You; nobody, for a permission boundary |
| `14021` `IP not whitelisted` | The session key has an IP allowlist without this agent's address. | The user |

`11022` is an order's `reject_timestamp` passing, not a stale session
timestamp, whatever older notes say.

## Orders, transfers and withdrawals

Not through this credential; this app is read-only. Every state-changing action
carries its own EIP-712 signature in the body (see
<https://docs.derive.xyz/authentication/action-signing.md>), on top of the
session headers. The two sign different digests. The proxy signs one digest
per request, so no request through it can carry both. Tell the user an order
has to be placed in Derive's own interface or SDK. Do not try to get round
this.

## What this is not

Derive is a venue in its own right. A Paradigm block trade or RFQ is a
different skill even if it settles somewhere similar.
