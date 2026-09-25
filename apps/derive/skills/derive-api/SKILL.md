---
name: derive-api
description: >
  Trading and account access on Derive (v2, formerly Lyra), the onchain
  options and perps exchange, using an Ethereum wallet key held by the DIME
  credential proxy. Covers the whole flow: building the two digests Derive
  expects, getting them signed without ever holding the key, reading account
  state and positions, and placing and cancelling orders. Use for ANY request
  to api.lyra.finance or api-demo.lyra.finance — including "place an order on
  Derive", "what are my Derive positions", "cancel my Derive orders", or a 403
  from the credential proxy on a Derive call. Read this BEFORE calling Derive:
  it says what to send in place of the signature, which you cannot work out
  from Derive's own documentation. Derive was called Lyra and its API still
  answers on lyra.finance; both names mean this venue.
metadata:
  author: tradeparadigm
---

# Derive

Onchain options, perpetuals and spot. Public data is open; everything under
`/private/` is authenticated by a secp256k1 signature you produce.

You do not have the key. It is sealed in the credential proxy, and you ask the
proxy to sign a digest you have computed. Everything below is about computing
the right digest.

## Hosts

| Environment | Host |
|---|---|
| Mainnet | `api.lyra.finance` |
| Testnet | `api-demo.lyra.finance` |

Every call is `POST`, including reads. `/public/*` needs nothing. `/private/*`
needs the three headers below.

## What you were given

The credential arrives as `CRED_<NAME>` holding a `sign-…` placeholder, plus
`CRED_<NAME>_META` holding the non-secret facts:

| Key | What it is |
|---|---|
| `owner` | The account that owns the position on Derive |
| `signer` | The address whose key signs. The owner's, unless a session key was registered |
| `subaccount_id` | The subaccount actions are taken against |

## Asking the proxy to sign

Two things in the same request, and either alone is refused:

1. The 32-byte digest, base64-encoded, in `X-Dime-Sign-<name>`. Take `<name>`
   from the `CRED_<NAME>` variable this credential arrived as, lowercased with
   `_` written as `-`. The proxy strips this header before forwarding.
2. The placeholder, written where the signature belongs.

**Send the digest, not the message.** This scheme signs 32 bytes. Hand it
anything else and the proxy refuses and says so. That is Derive's own model,
not a quirk of ours — their signing library signs a raw hash too.

The signature comes back as `r||s||v` in hex, 130 characters, with `v` as 27
or 28. **Derive wants it `0x`-prefixed, so write `0x` in front of the
placeholder yourself** — the proxy substitutes only where the placeholder is.

## You cannot hash this with what is installed

Everything below needs **keccak256**, and nothing in the agent has it. Node's
`crypto` offers `sha3-256`, which is a DIFFERENT hash — NIST padding, not
Keccak padding. It returns a perfectly good 32 bytes, the proxy will sign
them, and Derive will reject the signature with nothing pointing at the cause.
Reaching for it is the single most expensive mistake available on this page.

Install `ethers` once, at the start of the task. It carries all three things
this venue needs — `keccak256`, the EIP-191 message hash, and the EIP-712
typed-data hash — so it replaces three separate dependencies:

```sh
cd ~/.openclaw/workspace && npm install --silent ethers
```

Then write the helper, once, and call it for every private request. The
version in the name is the skill version it came from; if only an older one is
there, this file changed:

```sh
mkdir -p ~/.openclaw/workspace/tools/derive
rm -f ~/.openclaw/workspace/tools/derive/derive-*.mjs
cat > ~/.openclaw/workspace/tools/derive/derive-1.0.2.mjs <<'EOF'
import { hashMessage } from 'ethers';

const V = Object.keys(process.env).find(k => (process.env[k] || '').startsWith('sign-derive'));
if (!V) { console.error('no Derive sign- credential in the environment'); process.exit(2); }

// _META may be absent entirely, so default it rather than parsing undefined.
const META = JSON.parse(process.env[V + '_META'] || '{}');
const OWNER = process.env.OWNER || META.owner;
if (!OWNER) { console.error('no owner address: pass OWNER=0x… or ask the user'); process.exit(2); }

const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');
const HOST = /TESTNET|DEMO/i.test(V) ? 'api-demo.lyra.finance' : 'api.lyra.finance';

// EIP-191 personal_sign over the timestamp STRING. hashMessage applies the
// "\x19Ethereum Signed Message:\n<len>" prefix and keccak256 for us — the two
// places this is easy to get wrong by hand.
const ts = Date.now().toString();
const digest = hashMessage(ts);

const res = await fetch(`https://${HOST}${process.env.TARGET}`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-LyraWallet': OWNER,
    'X-LyraTimestamp': ts,                  // the one that was signed
    'X-LyraSignature': '0x' + process.env[V],
    [HDR]: Buffer.from(digest.slice(2), 'hex').toString('base64'),
  },
  body: process.env.BODY || `{"wallet": "${OWNER}"}`,
});
console.log(res.status, await res.text());
EOF
```

## Start by finding the subaccount id

Almost every other private call needs a `subaccount_id`, and the credential
often does not carry one — it is an optional detail the user may have left
blank. Ask Derive rather than asking the user:

```sh
TARGET=/private/get_subaccounts node ~/.openclaw/workspace/tools/derive/derive-1.0.2.mjs
```

```
200 {"result": {"wallet": "0xEDE0…f7A4", "subaccount_ids": [73340]}, "id": "…"}
```

This is also the cheapest way to prove the whole signing path works, because
it needs nothing but the owner address. Everything after it is one line:

```sh
TARGET=/private/get_subaccount BODY='{"subaccount_id": 73340}' \
  node ~/.openclaw/workspace/tools/derive/derive-1.0.2.mjs
```

The `0x` in front of the placeholder is written by the helper, because the
proxy substitutes only where the placeholder itself sits and Derive wants the
prefix.

**When this block and reality disagree, reality wins.** Change the smallest
thing that makes it work, run it, and say in one line what you changed. Do not
stop to ask, and do not run it unchanged to prove it fails.

## Digest one: a private REST call

Every `/private/*` call carries three headers:

| Header | Value |
|---|---|
| `X-LyraWallet` | the `owner` address |
| `X-LyraTimestamp` | current time in **milliseconds**, as a decimal string |
| `X-LyraSignature` | the signature over that timestamp string |

The digest is EIP-191 `personal_sign` over the timestamp string — the string,
not the number:

```
msg    = "1790243017651"
digest = keccak256("\x19Ethereum Signed Message:\n" + len(msg) + msg)
```

`len(msg)` is the byte length as a decimal string, so `13` here.

```http
POST /private/get_account HTTP/1.1
Host: api.lyra.finance
Content-Type: application/json
X-Dime-Sign-derive-wallet-key: <base64 of the 32-byte digest>
X-LyraWallet: 0x1a2b…
X-LyraTimestamp: 1790243017651
X-LyraSignature: 0xsign-derive-wallet-key-7Kj2mQx9vBn4rTz8wLpAeF

{"subaccount_id": 12345}
```

The timestamp you sign and the timestamp you send must be the same one.
Derive rejects a stale one.

## Digest two: an order, transfer or withdrawal

These are onchain actions and are signed as EIP-712 typed data, in addition to
the session headers above. The signature goes in the request body as
`signature`.

```
actionHash = keccak256(abi.encode(
    ACTION_TYPEHASH,   // bytes32, from Derive's Protocol Constants
    subaccount_id,     // uint
    nonce,             // uint
    module_address,    // address, per action type
    keccak256(module_data),
    signature_expiry_sec,
    owner,             // address
    signer))           // address

digest = keccak256(0x1901 || DOMAIN_SEPARATOR || actionHash)
```

`DOMAIN_SEPARATOR`, `ACTION_TYPEHASH` and each `module_address` come from the
Protocol Constants table in Derive's docs, and differ between mainnet and
testnet. `module_data` is the ABI encoding of the action itself — the fields
and their order are per module, so read Derive's docs for the one you are
performing.

The `nonce` is `<UTC ms><3-6 random digits>`, for example `1695836058725001`.
`signature_expiry_sec` must be more than five minutes ahead.

The body carries `subaccount_id`, `nonce`, `signer`, `signature_expiry_sec`,
`signature`, and the module's own fields.

## If a call is refused

| What you see | What it means |
|---|---|
| Proxy says the payload is not a digest | You sent the message. Hash it first. |
| Derive rejects a signature that looks well formed | You hashed with `sha3-256` instead of `keccak256`. They are different hashes and both return 32 bytes, so nothing upstream can tell. Use `ethers`. |
| `JSON.parse` throws on `undefined` | `CRED_<NAME>_META` is not set. It is not required — default it to `{}` and pass `OWNER=0x…` instead |
| You have no `subaccount_id` | It is an optional detail and may be blank. Call `/private/get_subaccounts`; do not ask the user |
| Proxy 403 | The placeholder and the `X-Dime-Sign-…` header were not both present, or the path is not under `/private/` |
| Derive `14020`/`14021` | `X-LyraWallet` is missing or does not match the subaccount |
| Derive `11022` | The timestamp is stale, or you signed a different one from the one you sent |
| Derive rejects the signature on an action | Usually the wrong `ACTION_TYPEHASH`, `module_address` or `DOMAIN_SEPARATOR` for the environment |

## What this is not

Derive is a venue in its own right. A Paradigm block trade or RFQ is a
different skill even if it settles somewhere similar.
