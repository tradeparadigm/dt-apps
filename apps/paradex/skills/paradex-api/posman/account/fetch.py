# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["paradex-py==0.8.2"]
# ///
"""A posman adapter: one Paradex account, fetched once.

DIME Terminal's posman skill runs this as `uv run --quiet fetch.py` on the
account's interval and stores what it prints, unchanged. POSMAN.md beside it
says how to read each dataset. Nothing else runs it: an agent tests it with
`posman.mjs test --adapter paradex/account`.

What it reads from the environment (the runner sets all of it):
  POSMAN_CRED            the NAME of the variable holding the credential's
                         placeholder: CRED_PARADEX_<ENV>_JWT (the read-only
                         token) or CRED_PARADEX_<ENV>_PRIV_KEY (the signing key)
  <POSMAN_CRED>_META     the credential's public details: `account`, and for
                         the signing key `public_key`
  POSMAN_PARAM_ENVIRONMENT  optional: mainnet, testnet or nightly; by default
                         the one in the credential's label (paradex-<env>-<type>)

What it prints: one JSON object, Paradex's records as Paradex sends them:
  positions    GET /v1/positions, every page, closed positions left out
  open_orders  GET /v1/orders
  balances     GET /v1/balance
  account      GET /v1/account (the account summary: value, margin, ...)
A dataset that fails is named in `_errors` instead, so the others are kept.

THE TWO CREDENTIALS. The read-only token's placeholder (`cred-...`) goes in
`Authorization: Bearer`, and the credential proxy swaps in the token. The
signing key's (`sign-...`) cannot be sent as a token: the adapter first asks
Paradex for one at POST /v1/auth/<public_key>, which carries a STARK signature
of the auth message. The proxy makes that signature: the request carries the
message hash in `X-Dime-Sign-<label>` and the placeholder where the signature
goes, exactly as paradex-py's own `ExternalSignerAccount` with `HeaderSigner`
does (the client the Paradex MCP server signs through). paradex-py computes
that hash; the requests are made here with httpx, so a refusal keeps its
status code.

Exit 3: Paradex or the proxy refused the credential (401 or 403). Exit 2: the
source is set up wrong (no credential, an environment it cannot tell). Any
other failure, exit 1.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse

EXIT_CONFIG = 2
EXIT_AUTH = 3

# The app's environments (app.yaml), by the id a credential's label carries.
HOSTS = {
    "mainnet": "api.prod.paradex.trade",
    "testnet": "api.testnet.paradex.trade",
    "nightly": "api.nightly.paradex.trade",
}
MAX_PAGES = 10
TIMEOUT_S = 20
# How long the auth signature stays valid, as paradex-py signs it.
AUTH_EXPIRY_S = 24 * 60 * 60


class Refused(Exception):
    """A 401 or 403: the credential, not the request."""


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def credential(env: dict) -> tuple[str, str, dict, str]:
    """The credential's variable, its placeholder, its public details and its label."""
    name = env.get("POSMAN_CRED", "")
    if not name:
        fail("no credential: track this account with a Paradex credential (--cred CRED_PARADEX_...)", EXIT_CONFIG)
    value = env.get(name, "")
    if not value:
        fail(f"{name} is not among the agent's credentials", EXIT_CONFIG)
    try:
        meta = json.loads(env.get(f"{name}_META") or "{}")
    except ValueError:
        meta = {}
    label = name.removeprefix("CRED_").lower().replace("_", "-")
    return name, value, meta if isinstance(meta, dict) else {}, label


def base_url(env: dict, label: str) -> str:
    """The REST root for the account's environment."""
    # Tests only: a local stand-in for Paradex. Never set on a pod.
    if env.get("POSMAN_PARADEX_API_URL"):
        return env["POSMAN_PARADEX_API_URL"].rstrip("/")
    parts = label.split("-")
    wanted = env.get("POSMAN_PARAM_ENVIRONMENT") or (parts[1] if len(parts) >= 3 and parts[0] == "paradex" else "")
    if wanted not in HOSTS:
        fail(
            f"cannot tell the Paradex environment from the credential {label!r}: "
            f"add --param ENVIRONMENT=<{'|'.join(HOSTS)}>",
            EXIT_CONFIG,
        )
    return f"https://{HOSTS[wanted]}/v1"


def request(method: str, url: str, headers: dict | None = None) -> object:
    # httpx (paradex-py's own client) sends header names as written, so the
    # proxy sees X-Dime-Sign-<label> exactly; it reads SSL_CERT_FILE, which on
    # a pod names the bundle holding the proxy's CA.
    import httpx

    res = httpx.request(method, url, headers={"Accept": "application/json", **(headers or {})}, timeout=TIMEOUT_S)
    if res.status_code >= 300:
        text = res.text[:300].strip()
        path = urllib.parse.urlparse(url).path
        if res.status_code in (401, 403):
            raise Refused(f"{method} {path}: HTTP {res.status_code}: {text}")
        raise RuntimeError(f"{method} {path}: HTTP {res.status_code}: {text}")
    return res.json() if res.content else None


def chain_id(base: str) -> int:
    """The Starknet chain id the auth message is signed for, from the public system config."""
    config = request("GET", f"{base}/system/config")
    name = config.get("starknet_chain_id") if isinstance(config, dict) else None
    if not isinstance(name, str) or not name:
        raise RuntimeError("GET /v1/system/config: no starknet_chain_id")
    # paradex-py: int_from_bytes(config.starknet_chain_id.encode())
    return int.from_bytes(name.encode(), "big")


def auth_hash(chain: int, account: int, timestamp: int, expiry: int) -> int:
    """The auth message's typed-data hash, as paradex-py signs it."""
    from paradex_py.account.utils import typed_data_to_message_hash
    from paradex_py.message.auth import build_auth_message

    return typed_data_to_message_hash(build_auth_message(chain, timestamp, expiry), account)


def auth_headers(placeholder: str, label: str, account: int, message_hash: int, timestamp: int, expiry: int) -> dict:
    """POST /v1/auth's headers through the signing proxy: paradex-py's ExternalSignerAccount with a HeaderSigner."""
    return {
        "PARADEX-STARKNET-ACCOUNT": hex(account),
        "PARADEX-STARKNET-SIGNATURE": placeholder,
        "PARADEX-TIMESTAMP": str(timestamp),
        "PARADEX-SIGNATURE-EXPIRATION": str(expiry),
        f"X-Dime-Sign-{label}": base64.b64encode(message_hash.to_bytes(32, "big")).decode(),
    }


def token(base: str, placeholder: str, meta: dict, label: str) -> str:
    """What goes in `Authorization: Bearer`: the read-only token's placeholder, or a JWT the signing key obtains."""
    if not placeholder.startswith("sign-"):
        return placeholder
    try:
        account = int(str(meta.get("account", "")), 16)
        public_key = int(str(meta.get("public_key", "")), 16)
    except ValueError:
        fail(
            f"{label} has no account address or public key in its details: re-enrol it with both "
            "(Settings → Credentials)",
            EXIT_CONFIG,
        )
    now = int(time.time())
    expiry = now + AUTH_EXPIRY_S
    headers = auth_headers(placeholder, label, account, auth_hash(chain_id(base), account, now, expiry), now, expiry)
    answer = request("POST", f"{base}/auth/{hex(public_key)}", headers)
    jwt = answer.get("jwt_token") if isinstance(answer, dict) else None
    if not isinstance(jwt, str) or not jwt:
        raise RuntimeError("POST /v1/auth: no jwt_token in the answer")
    return jwt


def pages(base: str, path: str, auth: dict) -> list:
    """Every page of a paged read (`{results, next}`), as one list."""
    out: list = []
    query: dict = {}
    for _ in range(MAX_PAGES):
        qs = f"?{urllib.parse.urlencode(query)}" if query else ""
        body = request("GET", f"{base}{path}{qs}", auth)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise RuntimeError(f"GET /v1{path}: no results list")
        out.extend(results)
        nxt = body.get("next")
        if not nxt:
            break
        query = {"cursor": nxt}
    return out


def open_positions(base: str, auth: dict) -> list:
    # Paradex lists closed positions too (status CLOSED, size 0).
    return [p for p in pages(base, "/positions", auth) if not (isinstance(p, dict) and p.get("status") == "CLOSED")]


def main(env: dict) -> dict:
    _, placeholder, meta, label = credential(env)
    base = base_url(env, label)
    try:
        auth = {"Authorization": f"Bearer {token(base, placeholder, meta, label)}"}
    except Refused as err:
        fail(str(err), EXIT_AUTH)
    readers = {
        "positions": lambda: open_positions(base, auth),
        "open_orders": lambda: pages(base, "/orders", auth),
        "balances": lambda: pages(base, "/balance", auth),
        "account": lambda: request("GET", f"{base}/account", auth),
    }
    out: dict = {}
    errors: dict = {}
    for dataset, read in readers.items():
        try:
            out[dataset] = read()
        except Refused as err:
            fail(str(err), EXIT_AUTH)
        except Exception as err:  # noqa: BLE001 - any other failure is that dataset's error
            errors[dataset] = str(err)[:300]
    if errors:
        out["_errors"] = errors
    return out


if __name__ == "__main__":
    try:
        result = main(dict(os.environ))
    except Exception as err:  # noqa: BLE001 - say what failed, on stderr, and exit 1
        fail(f"{type(err).__name__}: {err}")
    print(json.dumps(result))
