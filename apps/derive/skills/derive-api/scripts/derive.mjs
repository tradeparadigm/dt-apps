// Every Derive call goes through here, public and private alike. The skill
// names it; nothing else writes it.
//
//   TARGET=/private/get_positions BODY='{"subaccount_id": 73340}' node derive.mjs
//   node derive.mjs subaccounts
//
// TARGET is the method's path, with or without Derive's /v3 base. BODY is the
// method's JSON params, {"wallet": <owner>} when absent; Derive ignores a
// field a method does not take. Prints `<status> <body>`. Exits 1 when Derive
// or the proxy refused the call, and 2 when it cannot start. DERIVE_UNSIGNED=1
// leaves out the X-Dime-Sign header, which is a diagnostic and nothing else.
//
// DERIVE ANSWERS HTTP 200 WITH AN ERROR. It is JSON-RPC: the payload is under
// `result`, a failure is an `error` object, and the status is 200 either way.
// So the exit code reads the body, not the status.
//
// EVERY CALL IS SIGNED, public ones included. A credential scoped to any
// endpoint makes the proxy refuse an unsigned public call with 403, and Derive
// ignores the three headers on a public route (checked against the live API),
// so signing costs nothing and works under either scope.
//
// `subaccounts` asks both public/get_subaccounts and private/get_subaccounts and
// prints the union. The two have been seen to disagree, and the one left out
// held the account's only position.

const all = Object.keys(process.env).filter(k => (process.env[k] || '').startsWith('sign-derive'));
const V = process.env.DERIVE_CRED || all[0];
if (!V) { console.error('no Derive sign- credential in the environment'); process.exit(2); }
if (all.length > 1 && !process.env.DERIVE_CRED) {
  console.error('several Derive credentials: ' + all.join(', ') + ' — re-run with DERIVE_CRED=<the one you want>');
  process.exit(2);
}
const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');

if (!(process.env[V] || '').startsWith('sign-derive')) {
  console.error(`DERIVE_CRED=${V} does not hold a Derive sign- placeholder. Pick one of: ${all.join(', ') || 'none set'}`);
  process.exit(2);
}

// _META may be absent, or not JSON. Neither is fatal while OWNER is given.
let META = {};
try { META = JSON.parse(process.env[V + '_META'] || '{}') || {}; } catch { META = {}; }
const OWNER = process.env.OWNER || META.owner;
if (!OWNER) {
  console.error(`no owner address: ${V}_META carries none. Re-run with OWNER=0x… (ask the user for their Derive wallet address)`);
  process.exit(2);
}

// The label says which environment the credential was enrolled for. Guessing
// when it does not would send a testnet key's call to mainnet or the reverse,
// and one wallet key can own accounts on both.
const HOST = (process.env.DERIVE_HOST
  || (/TESTNET|DEMO/i.test(V) ? 'testnet.api.derive.xyz' : /MAINNET/i.test(V) ? 'api.derive.xyz' : ''))
  .replace(/^https?:\/\//, '').replace(/\/.*$/, '');
if (!HOST) {
  console.error(`${V} does not say mainnet or testnet. Re-run with DERIVE_HOST=api.derive.xyz for mainnet or DERIVE_HOST=testnet.api.derive.xyz for testnet.`);
  process.exit(2);
}

// ---- keccak256 ----
//
// Nothing in the agent has it. Node's crypto offers sha3-256, which is NIST
// SHA-3: a different padding byte, so a different hash. It returns a perfectly
// good 32 bytes, the proxy signs them, and Derive recovers a stranger's address
// from the result (14026 Session key not found) with nothing pointing here.

const MASK = (1n << 64n) - 1n;
const ROT = new Array(25).fill(0n);
const RC = [];
{
  let x = 1, y = 0;
  for (let t = 0; t < 24; t++) {
    ROT[x + 5 * y] = BigInt(((t + 1) * (t + 2) / 2) % 64);
    [x, y] = [y, (2 * x + 3 * y) % 5];
  }
  let r = 1;
  for (let i = 0; i < 24; i++) {
    let c = 0n;
    for (let j = 0; j < 7; j++) {
      if (r & 1) c |= 1n << BigInt((1 << j) - 1);
      r = (r << 1) ^ (r & 0x80 ? 0x171 : 0);
    }
    RC.push(c);
  }
}

const rotl = (v, n) => ((v << n) | (v >> (64n - n))) & MASK;

function keccakF(s) {
  for (let round = 0; round < 24; round++) {
    const C = [0, 1, 2, 3, 4].map(x => s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20]);
    for (let x = 0; x < 5; x++) {
      const D = C[(x + 4) % 5] ^ rotl(C[(x + 1) % 5], 1n);
      for (let y = 0; y < 25; y += 5) s[x + y] ^= D;
    }
    const B = new Array(25);
    for (let x = 0; x < 5; x++) {
      for (let y = 0; y < 5; y++) B[y + 5 * ((2 * x + 3 * y) % 5)] = rotl(s[x + 5 * y], ROT[x + 5 * y]);
    }
    for (let x = 0; x < 5; x++) {
      for (let y = 0; y < 5; y++) {
        s[x + 5 * y] = B[x + 5 * y] ^ (~B[(x + 1) % 5 + 5 * y] & MASK & B[(x + 2) % 5 + 5 * y]);
      }
    }
    s[0] ^= RC[round];
  }
}

function keccak256(bytes) {
  const rate = 136;
  const msg = new Uint8Array(bytes.length + rate - (bytes.length % rate));
  msg.set(bytes);
  msg[bytes.length] ^= 0x01; // Keccak's padding. SHA-3 writes 0x06 here.
  msg[msg.length - 1] ^= 0x80;
  const s = new Array(25).fill(0n);
  for (let off = 0; off < msg.length; off += rate) {
    for (let i = 0; i < rate / 8; i++) {
      let lane = 0n;
      for (let b = 7; b >= 0; b--) lane = (lane << 8n) | BigInt(msg[off + 8 * i + b]);
      s[i] ^= lane;
    }
    keccakF(s);
  }
  const out = new Uint8Array(32);
  for (let i = 0; i < 32; i++) out[i] = Number((s[i >> 3] >> BigInt(8 * (i & 7))) & 0xffn);
  return out;
}

// EIP-191 personal_sign: the prefix, the message's byte length in decimal, the
// message. Derive's session signature is this over the timestamp STRING.
function hashMessage(text) {
  const msg = new TextEncoder().encode(text);
  const prefix = new TextEncoder().encode(`\x19Ethereum Signed Message:\n${msg.length}`);
  const both = new Uint8Array(prefix.length + msg.length);
  both.set(prefix);
  both.set(msg, prefix.length);
  return keccak256(both);
}

// ---- the call ----

async function call(target, body) {
  const path = '/v3/' + target.replace(/^\/+/, '').replace(/^v3\//, '');
  const ts = Date.now().toString();
  const headers = {
    'Content-Type': 'application/json',
    'X-DeriveWallet': OWNER,
    'X-DeriveTimestamp': ts, // the one that was signed
    'X-DeriveSignature': '0x' + process.env[V],
    [HDR]: Buffer.from(hashMessage(ts)).toString('base64'),
  };
  // The probe for "is the proxy in the path at all": the same call without the
  // digest. A proxy 403 says it is; Derive's same answer as before says it is not.
  if (process.env.DERIVE_UNSIGNED) delete headers[HDR];
  const res = await fetch(`https://${HOST}${path}`, { method: 'POST', headers, body });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* the proxy and Derive's edge answer in plain text or HTML */ }
  const proxy = res.headers.get('x-dime-credential-error');
  if (proxy) console.error(`proxy: x-dime-credential-error=${proxy} x-dime-credential-label=${res.headers.get('x-dime-credential-label') || ''}`);
  return { status: res.status, text, json, ok: res.ok && !!json && !json.error };
}

const body = process.env.BODY || JSON.stringify({ wallet: OWNER });

if (process.argv[2] === 'subaccounts') {
  const wallet = JSON.stringify({ wallet: OWNER });
  const [pub, priv] = await Promise.all([
    call('/public/get_subaccounts', wallet),
    call('/private/get_subaccounts', wallet),
  ]);
  const ids = r => (r.ok && Array.isArray(r.json.result?.subaccount_ids) ? r.json.result.subaccount_ids : null);
  const why = r => r.json?.error || `${r.status} ${r.text.slice(0, 300)}`;
  const a = ids(pub), b = ids(priv);
  const union = [...new Set([...(a || []), ...(b || [])])].sort((x, y) => x - y);
  console.log(JSON.stringify({
    wallet: OWNER,
    subaccount_ids: union,
    public: a ?? { error: why(pub) },
    private: b ?? { error: why(priv) },
  }));
  // The private half is the one that proves the credential works. Its failure
  // is the news even when the public half found ids.
  process.exit(b === null ? 1 : 0);
}

if (!process.env.TARGET) {
  console.error('set TARGET to the method path, e.g. TARGET=/private/get_positions, or run `node derive.mjs subaccounts`');
  process.exit(2);
}
const r = await call(process.env.TARGET, body);
console.log(r.status, r.text);
if (!r.ok) {
  if (r.json?.error) console.error(`Derive refused the call with HTTP ${r.status}: error ${r.json.error.code}. Look it up under "If a call is refused" in the skill.`);
  process.exit(1);
}
