---
name: paradigm-api
description: >
  Authenticated access to Paradigm's DRFQv2 REST API using an access key and a
  signing key held by the DIME credential proxy. Covers both credentials, the
  signed string Paradigm expects, and the RFQ surface: verifying auth, listing
  RFQs, creating one and cancelling one. Use for ANY request to
  api.prod.paradigm.trade or api.testnet.paradigm.trade, including "send a
  Paradigm block RFQ", "list my Paradigm RFQs", "cancel rfq_...", "is my
  Paradigm key working", or a 403 from the credential proxy on a Paradigm call.
  Read this BEFORE calling Paradigm: every authenticated call needs BOTH
  credentials and a signature over bytes you have to assemble in the right
  order, and the body you sign has to be the body you send.
metadata:
  author: tradeparadigm
---

# Paradigm

Paradigm is an institutional liquidity network for crypto derivatives. The RFQ
surface, DRFQv2, is where a desk requests a two-way price on a structure and
crosses it with a counterparty.

Your `AGENTS.md` already explains the placeholder mechanism in general: how
`CRED_<NAME>` and `CRED_<NAME>_META` work, what the `cred-` and `sign-`
prefixes mean, and how to name the `X-Dime-Sign-` header. This file assumes
that and covers only what is specific to Paradigm.

## Hosts

| Environment | REST base |
|---|---|
| Mainnet | `https://api.prod.paradigm.trade` |
| Testnet | `https://api.testnet.paradigm.trade` |

**The credential decides the host. The machine you are running on says
nothing about it.** A box
whose name contains `testnet` routinely holds a mainnet credential, and reading
the hostname as evidence gets you a real RFQ you believed was a paper one. The
credential's variable name is the only thing that answers this.

## What you hold

**Two credentials, and every authenticated call needs both.** This is the
difference from every other venue in the store, where the two credential types
are alternatives. Here they are halves of one thing: the access key says which
desk is calling and the signature proves the call.

- **`CRED_PARADIGM_<ENV>_ACCESS`** is the access key, as a `cred-` placeholder.
  Write it where the bearer token goes. The proxy swaps it for the real value.
- **`CRED_PARADIGM_<ENV>_SIGNING`** is the signing key, as a `sign-` placeholder.
  Write it where the signature goes and tell the proxy what to sign. You never
  see either secret.

The exact variable names depend on the labels chosen at enrolment, so read them
from the environment. The two share everything but their
last component, which is how you pair them: a `sign-` placeholder named
`CRED_PARADIGM_MAINNET_SIGNING` goes with the `cred-` one named
`CRED_PARADIGM_MAINNET_ACCESS`.

If only one of the two is in the environment, stop and say so. A call with one
half is refused by the proxy on the missing placeholder, or by Paradigm on the
missing header, and neither message names the enrolment as the problem.

## The three headers, and the string you sign

Every authenticated call carries:

```http
Authorization: Bearer cred-paradigm-mainnet-access-8Kq2mQx9vBn4rTz8
Paradigm-API-Timestamp: 1759000000000
Paradigm-API-Signature: sign-paradigm-mainnet-signing-4Rt7pLw2xCv9nMb1
```

The bytes the signature covers are these four fields joined by newlines, in
this order and nothing else:

```
<timestamp_ms>\n<METHOD>\n<path-with-query>\n<body>
```

Four things about that string that are each a wasted call if you get them
wrong:

- **The timestamp is the same value in both places.** The one in the signed
  bytes and the one in `Paradigm-API-Timestamp` have to match. Generating it
  twice is how they stop matching.
- **`<METHOD>` is upper case.** `get` is not `GET`.
- **The path carries its query string**, exactly as it appears on the URL. This
  is unlike Bybit, where the query is signed apart from the path.
- **`<body>` is the raw bytes you are about to send**, and empty for a GET or a
  DELETE. Serialising the object once to sign and again to send inserts
  whitespace and the signature no longer covers what arrived.

You send the bytes to be signed base64-encoded in the `X-Dime-Sign-<label>`
header, and the proxy strips that header before forwarding. Base64 is what
makes the newlines transportable: a header value cannot carry a raw newline.

Timestamps outside 30 seconds of Paradigm's clock are refused, so build the
timestamp at the moment of the call. A reused one is a refusal.

## The helper

**Check for the helper before you build anything**, then call it for every
request. Do not paste a block per call, and do not build the request out of
`curl`: a JSON body inside shell quoting cannot survive an apostrophe or a
computed value, which is exactly where byte-exactness dies.

```sh
ls ~/.openclaw/workspace/tools/paradigm/paradigm-api/paradigm-api-1.0.0.mjs
```

If that file is there, an earlier chat already wrote it, so skip to the calls
below. If it is not, run this:

```sh
rm -rf ~/.openclaw/workspace/tools/paradigm/paradigm-api
mkdir -p ~/.openclaw/workspace/tools/paradigm/paradigm-api
find ~/.openclaw/workspace/tools/paradigm -maxdepth 1 -name '*.mjs' -delete
cat > ~/.openclaw/workspace/tools/paradigm/paradigm-api/paradigm-api-1.0.0.mjs <<'EOF'
// Two credentials, and they have to be the same Paradigm key on the same
// environment. The variable NAME is what says so: it is derived from the label,
// and the two labels differ only in their last component. The placeholder VALUE
// cannot be parsed for it, because the random suffix is base64url and may
// itself contain a hyphen.
//
// Every ambiguous case below refuses and names the override. Guessing here
// would pair a testnet key with a mainnet one and place a real trade that was
// meant to be paper.
const sign = k => (process.env[k] || '').startsWith('sign-');
const cred = k => (process.env[k] || '').startsWith('cred-');
const bail = m => { console.error(m); process.exit(2); };

const signs = Object.keys(process.env).filter(k => /^CRED_.*PARADIGM/i.test(k) && sign(k));
const V = process.env.PARADIGM_SIGN || signs[0];
if (!V) bail('no Paradigm sign- credential in the environment. Set PARADIGM_SIGN=<var> if its label does not contain "paradigm".');
if (signs.length > 1 && !process.env.PARADIGM_SIGN) {
  bail('several Paradigm signing credentials: ' + signs.join(', ') + '. Re-run with PARADIGM_SIGN=<the one you want>.');
}

const STEM = V.replace(/_[^_]+$/, '');
let A = process.env.PARADIGM_ACCESS;
if (!A) {
  if (!STEM.includes('_')) {
    bail(V + ' has no environment in its name, so its access key cannot be identified. Re-run with PARADIGM_ACCESS=<var>.');
  }
  const hits = Object.keys(process.env).filter(k => k.startsWith(STEM + '_') && cred(k));
  if (hits.length === 0) bail('found the signing credential ' + V + ' and no access key under ' + STEM + '_*. Both go on every call.');
  if (hits.length > 1) bail('several access keys under ' + STEM + '_*: ' + hits.join(', ') + '. Re-run with PARADIGM_ACCESS=<the one you want>.');
  A = hits[0];
}

// The host comes from the same name, and an environment it cannot read is a
// refusal. Defaulting to mainnet here is how a paper trade becomes a real one.
let HOST = process.env.HOST;
if (!HOST) {
  if (/TESTNET/i.test(V)) HOST = 'api.testnet.paradigm.trade';
  else if (/MAINNET|PROD/i.test(V)) HOST = 'api.prod.paradigm.trade';
  else bail(V + ' does not name an environment, so the host cannot be chosen. Re-run with HOST=api.prod.paradigm.trade or HOST=api.testnet.paradigm.trade.');
}
// The access key has to be the same environment as the signing key.
if (!process.env.PARADIGM_ACCESS && A.replace(/_[^_]+$/, '') !== STEM) {
  bail('the access key ' + A + ' is not the same credential set as ' + V + '.');
}

const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');
const method = (process.env.METHOD || 'GET').toUpperCase();
const body = process.env.BODY || '';
// TARGET carries the query, and the signed path carries it too. One source, so
// the URL and the signed bytes cannot disagree.
const target = process.env.TARGET || '';
const ts = Date.now().toString();
const payload = [ts, method, target, body].join('\n');

const res = await fetch(`https://${HOST}${target}`, {
  method,
  headers: {
    'Authorization': 'Bearer ' + process.env[A],
    'Paradigm-API-Timestamp': ts,
    'Paradigm-API-Signature': process.env[V],
    [HDR]: Buffer.from(payload).toString('base64'),
    ...(body ? { 'Content-Type': 'application/json' } : {}),
  },
  ...(body ? { body } : {}),
});
console.log(res.status, await res.text());
EOF
```

`tools/paradigm/paradigm-api/` belongs to this skill and holds this one file.
The `rm -rf` clears it, so a script an earlier chat wrote there goes too. Do
not put your own scripts in it.

Never patch this helper when it breaks. Delete it and write it again from this
file. A patched helper quietly stops matching the published skill, and then
nobody can tell which of the two is running.

**It refuses where it cannot be certain, and every refusal names its override.**
The two credentials have to be the same Paradigm key on the same environment,
and the variable names are what say so. The placeholder values cannot be read
for it: the random suffix is base64url and may contain a hyphen, so there is no
reliable way to cut the label out of one. So when the names do not settle it the
helper stops and tells you which variable to set:

| It says | You set |
|---|---|
| no Paradigm sign- credential | `PARADIGM_SIGN=<var>`, when the label does not contain "paradigm" |
| several signing credentials | `PARADIGM_SIGN=<var>` |
| no access key under `<stem>_*`, or several | `PARADIGM_ACCESS=<var>` |
| the name has no environment in it | `HOST=api.prod.paradigm.trade` or `HOST=api.testnet.paradigm.trade` |

**It never picks mainnet as a fallback.** An environment it cannot read is a
refusal, because a wrong guess there is a real trade that was meant to be paper.

## Calls

Every call after that is one line. `TARGET` is the path with its query, and
`BODY` is the raw JSON for a write.

**Verify the credentials first.** `echo` is the endpoint for it: a 200 means
the access key, the signature, the headers and the transport are all correct,
and it changes nothing.

```sh
H=~/.openclaw/workspace/tools/paradigm/paradigm-api/paradigm-api-1.0.0.mjs

METHOD=GET TARGET=/v2/drfq/echo/ node $H

METHOD=GET TARGET=/v2/drfq/rfqs/ node $H

METHOD=POST TARGET=/v2/drfq/rfqs/ \
  BODY='{"venue":"PRDX","legs":[{"instrument":"BTC-USD-PERP","quantity":"1","side":"BUY"}]}' \
  node $H

METHOD=DELETE TARGET=/v2/drfq/rfqs/rfq_abc node $H
```

The trailing slash on these paths is Paradigm's own, and it is part of the
signed path. Dropping it changes both the URL and the bytes.

**Creating an RFQ commits the desk to a price.** Confirm the structure, the
venue and the size with the user before sending a POST, and say which
environment the credential is scoped to when you do.

## When it fails

Read the status and the body together, and check these in order.

| Symptom | Causes, cheapest first |
|---|---|
| 403 from the proxy, body naming a placeholder | The placeholder for that credential was not in the request. Both halves go on every call. |
| 403 from the proxy, body naming the `X-Dime-Sign-` header | The header was absent, or its value was not base64. Send the base64 encoding of the raw bytes. The bytes themselves are refused, and so is a JSON string. |
| 403 from Paradigm, body `Invalid API Access Key` | The placeholder reached Paradigm unsubstituted, so no credential rule matched the request. The usual cause is a path outside `/v2/drfq`, which is the only surface these credentials are scoped to. |
| 401 from Paradigm | The signed bytes are wrong: a mismatched timestamp, a lower-case method, a query string signed in the wrong place, or a body re-serialised after signing. It also means a stale timestamp, which is the one to check first if the call worked a minute ago. |
| 401 from Paradigm, and `echo` also fails | The access key and the signing key are from different Paradigm keys. Both have to come from the same one. |
| 404 from Paradigm | A missing path. This is nothing to do with the credentials, so check the trailing slash. |
| 400 from Paradigm on a POST | A 400 means the signature was accepted. The body is what Paradigm is objecting to. |

The two 403s are different failures and the body tells them apart. A refusal
from the proxy names the placeholder or the header it was looking for. A refusal
from Paradigm is JSON with a `detail` field, and it means the request reached the
venue carrying a placeholder that was never substituted.

## What is not here

This skill is the REST surface and its authentication. The RFQ workflow itself,
which venues are eligible, how to benchmark a structure and how to run a
confirmation gate, is a separate skill.
