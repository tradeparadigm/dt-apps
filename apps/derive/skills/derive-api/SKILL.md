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
| Proxy 403 | The placeholder and the `X-Dime-Sign-…` header were not both present, or the path is not under `/private/` |
| Derive `14020`/`14021` | `X-LyraWallet` is missing or does not match the subaccount |
| Derive `11022` | The timestamp is stale, or you signed a different one from the one you sent |
| Derive rejects the signature on an action | Usually the wrong `ACTION_TYPEHASH`, `module_address` or `DOMAIN_SEPARATOR` for the environment |

## What this is not

Derive is a venue in its own right. A Paradigm block trade or RFQ is a
different skill even if it settles somewhere similar.
