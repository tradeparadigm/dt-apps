"""The Derive client the skill ships: scripts/derive.mjs.

Two halves. The hash is checked against vectors pinned from ethers, through the
client itself (the digest it hands the proxy for a fixed clock) and through its
keccak256 alone, across Keccak's 136-byte block boundary. Python's hashlib has
no Keccak (its sha3_256 is the NIST hash the skill warns about), so the vectors
are pinned rather than recomputed here.

The request half runs the client under node with fetch replaced by a stub that
records each request and answers as Derive or the proxy would, so nothing here
reaches Derive. The live run that these stand in for is described in the PR.
Node 22 or later.
"""

import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

CLIENT = pathlib.Path(__file__).resolve().parents[4] / "apps/derive/skills/derive-api/scripts/derive.mjs"

OWNER = "0x1a2B3c4D5e6F708192a3B4c5D6e7F8091a2b3C4d"
PLACEHOLDER = "sign-derive-mainnet-wallet-key-7Kj2mQx9vBn4rTz8wLpAeF"
MAINNET = "CRED_DERIVE_MAINNET_WALLET_KEY"

# EIP-191 personal_sign digests of the timestamp string, from ethers' hashMessage.
DIGESTS = {
    "0": "M7OjtqWSM1mJqRxR/aEk1azaLhmCErg4WJJR1DG5xP4=",
    "1790243017651": "Z073cfXnTBC45Qq1JraMweURc0hH4027TtizwcnzyAQ=",
    "1793958175819": "Ok7evoF45o0YNGud5UJR6CQwvDjR/upD3EZpzhOJ0qc=",
}

# keccak256, from ethers. seqN is bytes 0..N-1 mod 256; 136 bytes is one block.
KECCAK = {
    "empty": "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
    "abc": "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
    "seq135": "cbdfd9dee5faad3818d6b06f95a219fd290b0e1706f6a82e5a595b9ce9faca62",
    "seq136": "7ce759f1ab7f9ce437719970c26b0a66ff11fe3e38e17df89cf5d29c7d7f807e",
    "seq137": "ac73d4fae68b8453f764007c1a20ce95994187861f0c3227a3a8e99a73a3b1db",
    "seq300": "a679e749a6af300c36e7ff2255d220864eab27b382f9cfdc5aa4d13563ba36ff",
    "a272": "cf7fcd4f705ee749930d19ca84561a9bf62516bd90a471545fa2f49fdc7e63c8",
}

STUB = r"""
import fs from 'node:fs';
if (process.env.FAKE_NOW) Date.now = () => Number(process.env.FAKE_NOW);
const replies = JSON.parse(process.env.FAKE_REPLIES || '{}');
globalThis.fetch = async (url, init) => {
  fs.appendFileSync(process.env.FAKE_LOG, JSON.stringify({
    url: String(url), method: init.method, headers: init.headers, body: init.body,
  }) + '\n');
  const path = new URL(url).pathname;
  const r = replies[path] || replies['*'] || { status: 200, body: '{"id":"1","result":{}}' };
  return new Response(r.body, { status: r.status, headers: r.headers || {} });
};
"""

passed = 0
failed = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(f"{name}{': ' + detail if detail else ''}")


TMP = pathlib.Path(tempfile.mkdtemp(prefix="derive-client-"))
(TMP / "stub.mjs").write_text(STUB)


def run(env=None, args=(), replies=None, now="1790243017651", cred=True):
    """Run the client once. Returns (exit code, stdout, stderr, requests)."""
    log = TMP / "requests.jsonl"
    if log.exists():
        log.unlink()
    full = {"PATH": os.environ["PATH"], "HOME": str(TMP), "FAKE_LOG": str(log),
            "FAKE_REPLIES": json.dumps(replies or {}), "FAKE_NOW": now}
    if cred:
        full[MAINNET] = PLACEHOLDER
        full[MAINNET + "_META"] = json.dumps({"owner": OWNER, "signer": OWNER, "subaccount_id": "73340"})
    full.update(env or {})
    full = {k: v for k, v in full.items() if v is not None}
    p = subprocess.run(["node", "--import", str(TMP / "stub.mjs"), str(CLIENT), *args],
                       env=full, capture_output=True, text=True, timeout=60)
    reqs = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return p.returncode, p.stdout, p.stderr, reqs


def rpc(result=None, error=None):
    body = {"id": "1", "result": result} if error is None else {"id": "1", "error": error}
    return {"status": 200, "body": json.dumps(body)}


# ---- the hash ----

def keccak_alone():
    src = CLIENT.read_text()
    start, end = src.index("// ---- keccak256 ----"), src.index("// ---- the call ----")
    mod = TMP / "keccak.mjs"
    mod.write_text(src[start:end] + "\nexport { keccak256 };\n")
    script = r"""
import { keccak256 } from __MOD__;
const seq = n => Uint8Array.from({ length: n }, (_, i) => i % 256);
const enc = s => new TextEncoder().encode(s);
const inputs = { empty: new Uint8Array(), abc: enc('abc'), seq135: seq(135), seq136: seq(136),
                 seq137: seq(137), seq300: seq(300), a272: enc('a'.repeat(272)) };
const out = {};
for (const [k, v] of Object.entries(inputs)) out[k] = Buffer.from(keccak256(v)).toString('hex');
console.log(JSON.stringify(out));
""".replace("__MOD__", json.dumps(mod.as_uri()))
    p = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    got = json.loads(p.stdout) if p.returncode == 0 else {}
    for k, want in KECCAK.items():
        check(f"keccak256 {k}", got.get(k) == want, f"got {got.get(k)} ({p.stderr.strip()[:200]})")


keccak_alone()

for ts, want in DIGESTS.items():
    code, out, err, reqs = run(env={"TARGET": "/private/get_account"}, now=ts)
    hdr = reqs[0]["headers"].get("X-Dime-Sign-derive-mainnet-wallet-key") if reqs else None
    check(f"digest of {ts!r} is EIP-191 keccak", hdr == want, f"got {hdr}")
    check(f"timestamp sent is the one signed ({ts})", reqs and reqs[0]["headers"].get("X-DeriveTimestamp") == ts)


# ---- the request ----

code, out, err, reqs = run(env={"TARGET": "/private/get_positions", "BODY": '{"subaccount_id": 73340}'},
                           replies={"*": rpc({"subaccount_id": 73340, "positions": []})})
check("success exits 0", code == 0, err)
check("prints status and body", out.startswith("200 ") and '"positions"' in out, out)
check("one request", len(reqs) == 1)
r = reqs[0]
check("mainnet host with the /v3 base", r["url"] == "https://api.derive.xyz/v3/private/get_positions", r["url"])
check("POST", r["method"] == "POST")
check("X-DeriveWallet is the owner", r["headers"].get("X-DeriveWallet") == OWNER)
check("X-DeriveSignature is 0x and the placeholder", r["headers"].get("X-DeriveSignature") == "0x" + PLACEHOLDER)
check("no X-Lyra header", not any(k.lower().startswith("x-lyra") for k in r["headers"]), str(r["headers"]))
check("BODY sent as given", r["body"] == '{"subaccount_id": 73340}')
check("digest is 32 bytes", len(base64.b64decode(r["headers"].get("X-Dime-Sign-derive-mainnet-wallet-key", ""))) == 32)

for target in ("/v3/private/get_positions", "private/get_positions", "v3/private/get_positions"):
    _, _, _, reqs = run(env={"TARGET": target})
    check(f"TARGET {target!r} gets one /v3", reqs and reqs[0]["url"] == "https://api.derive.xyz/v3/private/get_positions",
          reqs[0]["url"] if reqs else "no request")

_, _, _, reqs = run(env={"TARGET": "/private/get_subaccounts"})
check("BODY defaults to the owner's wallet", reqs and json.loads(reqs[0]["body"]) == {"wallet": OWNER})

_, _, _, reqs = run(env={"TARGET": "/private/get_account", "DERIVE_UNSIGNED": "1"})
check("DERIVE_UNSIGNED drops the digest and keeps the rest", reqs
      and "X-Dime-Sign-derive-mainnet-wallet-key" not in reqs[0]["headers"]
      and reqs[0]["headers"].get("X-DeriveSignature") == "0x" + PLACEHOLDER)

code, _, _, reqs = run(env={"TARGET": "/public/get_ticker", "BODY": '{"instrument_name": "ETH-PERP"}'})
check("a public call is signed too", code == 0 and reqs and reqs[0]["headers"].get("X-DeriveSignature") == "0x" + PLACEHOLDER
      and "X-Dime-Sign-derive-mainnet-wallet-key" in reqs[0]["headers"])
check("a public call has the /v3 base", reqs and reqs[0]["url"] == "https://api.derive.xyz/v3/public/get_ticker")

# ---- choosing the host ----

testnet = "CRED_DERIVE_TESTNET_WALLET_KEY"
_, _, _, reqs = run(env={"TARGET": "/private/get_account", MAINNET: None, MAINNET + "_META": None,
                         testnet: "sign-derive-testnet-wallet-key-x", testnet + "_META": json.dumps({"owner": OWNER})})
check("a TESTNET credential goes to testnet", reqs and reqs[0]["url"].startswith("https://testnet.api.derive.xyz/v3/"))
check("its sign header is named for it", reqs and "X-Dime-Sign-derive-testnet-wallet-key" in reqs[0]["headers"])

other = "CRED_DERIVE_TRADING_KEY"
code, _, err, reqs = run(env={"TARGET": "/private/get_account", MAINNET: None, MAINNET + "_META": None,
                              other: "sign-derive-trading-key-x", "OWNER": OWNER})
check("a name that says no environment is refused", code == 2 and not reqs and "DERIVE_HOST" in err, err)
code, _, _, reqs = run(env={"TARGET": "/private/get_account", MAINNET: None, MAINNET + "_META": None,
                            other: "sign-derive-trading-key-x", "OWNER": OWNER,
                            "DERIVE_HOST": "https://testnet.api.derive.xyz/v3"})
check("DERIVE_HOST decides, written as a URL or not", code == 0 and reqs
      and reqs[0]["url"] == "https://testnet.api.derive.xyz/v3/private/get_account", reqs[0]["url"] if reqs else "")

# ---- refusals ----

code, out, err, _ = run(env={"TARGET": "/private/get_account"}, replies={"*": rpc(error={
    "code": 14014, "message": "Signature invalid for message or transaction", "data": "Signature not formatted correctly"})})
check("a JSON-RPC error with HTTP 200 exits 1", code == 1, f"exit {code}")
check("the error body is printed", "Signature not formatted correctly" in out, out)
check("the code is named on stderr", "14014" in err, err)

code, out, err, _ = run(env={"TARGET": "/private/get_account"}, replies={"*": {
    "status": 403, "body": "credential placeholder absent",
    "headers": {"x-dime-credential-error": "placeholder_absent", "x-dime-credential-label": "derive-mainnet-wallet-key"}}})
check("a proxy 403 exits 1", code == 1)
check("the proxy's machine-readable reason is printed", "placeholder_absent" in err and "derive-mainnet-wallet-key" in err, err)

code, out, _, _ = run(env={"TARGET": "/private/get_account"}, replies={"*": {
    "status": 403, "body": "<html><center>derive</center></html>"}})
check("Derive's edge 403 (HTML) exits 1", code == 1 and out.startswith("403 "), out)

code, _, _, _ = run(env={"TARGET": "/private/get_account"}, replies={"*": {"status": 503, "body": "busy"}})
check("a 5xx exits 1", code == 1)

# ---- starting up ----

code, _, err, reqs = run(env={"TARGET": "/private/get_account"}, cred=False)
check("no credential exits 2", code == 2 and not reqs and "no Derive sign- credential" in err, err)

two = {"TARGET": "/private/get_account", "CRED_DERIVE_TESTNET_WALLET_KEY": "sign-derive-testnet-wallet-key-y"}
code, _, err, reqs = run(env=two)
check("two credentials exit 2 rather than pick one", code == 2 and not reqs and "DERIVE_CRED" in err, err)
code, _, _, reqs = run(env={**two, "DERIVE_CRED": MAINNET})
check("DERIVE_CRED picks one", code == 0 and reqs and reqs[0]["headers"].get("X-DeriveSignature") == "0x" + PLACEHOLDER)
code, _, err, reqs = run(env={**two, "DERIVE_CRED": "CRED_NOT_THERE"})
check("DERIVE_CRED naming nothing exits 2", code == 2 and not reqs, err)

code, _, err, reqs = run(env={"TARGET": "/private/get_account", MAINNET + "_META": None})
check("no _META and no OWNER exits 2", code == 2 and not reqs and "OWNER" in err, err)
code, _, _, reqs = run(env={"TARGET": "/private/get_account", MAINNET + "_META": None, "OWNER": OWNER})
check("no _META with OWNER works", code == 0 and reqs and reqs[0]["headers"].get("X-DeriveWallet") == OWNER)
code, _, _, reqs = run(env={"TARGET": "/private/get_account", MAINNET + "_META": "not json", "OWNER": OWNER})
check("unreadable _META with OWNER works", code == 0 and len(reqs) == 1)

code, _, err, reqs = run(env={})
check("no TARGET exits 2", code == 2 and not reqs and "TARGET" in err, err)


# ---- subaccounts: the union ----

def subaccounts(public, private):
    replies = {"/v3/public/get_subaccounts": public, "/v3/private/get_subaccounts": private}
    code, out, err, reqs = run(args=["subaccounts"], replies=replies)
    try:
        doc = json.loads(out)
    except ValueError:
        doc = {}
    return code, doc, reqs


code, doc, reqs = subaccounts(rpc({"wallet": OWNER, "subaccount_ids": [120388, 120387]}),
                              rpc({"wallet": OWNER, "subaccount_ids": [120387]}))
check("subaccounts takes the union, sorted", doc.get("subaccount_ids") == [120387, 120388], str(doc))
check("subaccounts shows each side", doc.get("public") == [120388, 120387] and doc.get("private") == [120387])
check("subaccounts exits 0 when the private side answered", code == 0)
check("subaccounts asks both, each signed, with the wallet",
      sorted(r["url"] for r in reqs) == ["https://api.derive.xyz/v3/private/get_subaccounts",
                                         "https://api.derive.xyz/v3/public/get_subaccounts"]
      and all(json.loads(r["body"]) == {"wallet": OWNER} and "X-Dime-Sign-derive-mainnet-wallet-key" in r["headers"]
              for r in reqs))

code, doc, _ = subaccounts(rpc({"wallet": OWNER, "subaccount_ids": [7]}),
                           rpc(error={"code": 14026, "message": "Session key not found", "data": "address: 0xabc"}))
check("a private failure keeps the public ids", doc.get("subaccount_ids") == [7], str(doc))
check("a private failure is reported", doc.get("private", {}).get("error", {}).get("code") == 14026, str(doc))
check("a private failure exits 1", code == 1)

code, doc, _ = subaccounts({"status": 403, "body": "refused"}, rpc({"wallet": OWNER, "subaccount_ids": [9]}))
check("a public failure still exits 0", code == 0 and doc.get("subaccount_ids") == [9], str(doc))
check("a public failure is reported", "403" in str(doc.get("public", {}).get("error")), str(doc))


shutil.rmtree(TMP, ignore_errors=True)
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print("  FAIL", f)
sys.exit(1 if failed else 0)
