#!/usr/bin/env python3
"""Check every app in this repo is shaped the way DIME Terminal reads it.

THIS REPO IS THE STORE. DIME Terminal fetches it at run time, so a malformed
app here is not caught by anyone's build — it is dropped from a live catalogue
with a line in a log nobody reads. The checks that used to happen at someone
else's deploy therefore happen here, against the pull request that made the
mistake.

THIS PARSES YAML RATHER THAN SCANNING IT, and the difference is not academic.
An earlier version scanned with regexes to keep the workflow to one stdlib
invocation, and review found what that costs: `summary: >-` with no body read
as the literal string ">-" and satisfied the required-field check, while the
consumer's parser resolves it to "" and refuses the app. The checker's whole
job is to agree with a YAML reader, and the only way to do that reliably is to
be one. The workflow installs PyYAML; that is the price.

What is checked, in each case because the consumer refuses the app without it:

  * apps/<id>/app.yaml exists, and its `id` matches the directory.
  * The manifest carries what the catalogue renders: version, name, blurb,
    description, at least one environment and one credential type.
  * Every environment and credential type carries its own required fields, its
    ids are well formed as well as present, and ids, hosts and slugs are
    unique. These live in the consumer's App.validate, and a manifest missing
    one used to merge here and vanish from the live catalogue.
  * Hosts are bare lowercase hostnames — no scheme, port, path or underscore.
    The proxy matches them case-sensitively, so an uppercase letter is a rule
    that can never fire, and the enrolment path refuses anything that is not a
    hostname outright.
  * Routes carry a path the API accepts and methods from the closed set,
    already upper case and without duplicates. The consumer canonicalises and
    refuses any difference, so `get` and a repeated path are both refusals
    rather than tidy-ups.
  * Every app states its scope on two axes: `access` (read-only or read-write)
    and `maturity` (stable or beta). Required here though the consumer defaults
    both, because the default is the permissive pair — an author who omitted
    them would publish a writable, stable-looking app without having said so.
  * Optional branding — developer, developer_url, tint, icon — is safe to put
    on a page. The store takes pull requests from outside this team, so a tint
    is a hex colour and an icon is ONE SVG PATH rather than a file: a path is
    geometry, and cannot execute, fetch, or escape the box it is drawn into.
  * Every key is one the consumer knows. Its decoder runs with KnownFields,
    so an unknown key — a typo, a field from a newer schema — refuses the
    whole app rather than being ignored.
  * A credential type names a delivery mode, a signing scheme, a signature
    encoding and a key encoding that the proxy actually has a case for. A
    manifest may describe an app; it cannot invent a capability. The key
    encoding is how the stored key is READ before signing with it, which the
    signature encoding does not cover, and only the MAC scheme reads one.
  * A delivery carries only the fields its mode uses. The consumer compares
    what a manifest declared against what canonicalising it produced and
    refuses a difference, because a field silently dropped is a line a reviewer
    approved that does not run — `mode: inject` beside `match_body: true`
    reads as a credential that scans the body and is stored as one that does
    not.
  * A credential type narrows itself to no more endpoints than the API accepts.
  * detail_fields carry a key the API can write into public metadata, and a
    label, because the label is what the enrolment form shows.
  * Nothing is a symlink. DIME Terminal reads this repo as a tarball and
    refuses a link outright, because a link is a file whose contents are a
    path: followed, it names something outside the tree; skipped, it empties a
    skill directory and the app loads as one that teaches nothing, which
    deletes its skill off every agent that installed it.
  * AT LEAST ONE SKILL PER APP, and at most MAX_SKILLS. Zero is refused because
    a publish is a full replacement, an app contributing no files is dropped
    from the set, and so an app that ships no skill is byte-identical to one
    whose files did not survive the fetch — which deletes a working skill off
    every agent that installed it. The cap is on SKILLS because a skill is
    prose plus its references, MAX_FILES is what one of those needs, and the
    per-app file ceiling is the product.
  * Every directory under skills/ holds a SKILL.md whose frontmatter `name`
    equals the directory, with a non-empty description.
  * An optional mcp_server names only this app's credential types and
    environments, sets no env name the sidecar reserves, and uses templates
    only where a credential fills them.
  * No two apps claim the same skill name. openclaw resolves a collision by
    precedence rather than erroring, so one app's skill would simply never
    load and nothing would say so. The consumer has a backstop — it keeps the
    app that already held the name and refuses the newcomer — but that
    incumbency lives in a process's memory, so a replica that started after
    the collision has no record of who was first and refuses both. The only
    place this can be settled for everyone is here, in the pull request that
    introduces it.

The manifest names NO skills. The directory is the content, the id is the
join, and there is nothing for two files to disagree about.

Exit 0 = all good, 1 = at least one violation.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
import sys
from pathlib import Path

import yaml

APPS = Path("apps")
MANIFEST = "app.yaml"
ENTRYPOINT = "SKILL.md"

# Mirrored from pkg/apps: tintPattern, iconPathPattern, viewBoxPattern and
# maxIconPathBytes. The icon is a path and not a file on purpose — see the
# docstring.
TINT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Go's RE2 \s is exactly [\t\n\f\r ]. Python's is wider — it also matches the
# vertical tab and a pile of Unicode spaces — and wider here means a manifest
# passes this check and is then dropped by the consumer.
_WS = r"[\t\n\f\r ]"
ICON_PATH_RE = re.compile(r"^[MmZzLlHhVvCcSsQqTtAa0-9eE,.\t\n\f\r +-]+$")
VIEW_BOX_RE = re.compile(
    rf"^-?[0-9.]+{_WS}+-?[0-9.]+{_WS}+-?[0-9.]+{_WS}+-?[0-9.]+$"
)
MAX_ICON_PATH = 8 * 1024

# Mirrored from web-api/services/credentials.go: hostPattern, pathPattern,
# knownHTTPMethods, maxAllowedMethodsPerCredential. A manifest goes through the
# same canonicalisation a typed-in credential does, and the consumer refuses
# any difference between what was declared and what canonicalising produced.
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"}
MAX_METHODS = 8

# The cached helper path a skill names. Nothing in the consumer reads it, and
# the filename is the whole staleness mechanism, so a `version:` bump that
# leaves the filename alone is only catchable here.
HELPER_RE = re.compile(
    r"tools/(?P<app>[a-z0-9-]+)/(?P<skill>[a-z0-9-]+)/(?P=skill)-(?P<version>[^/\s`'\"]+)\.mjs"
)

# Mirrored from pkg/apps: idPattern, slugPattern, detailKeyPattern.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
DETAIL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}[a-z0-9]$|^[a-z0-9]$")

# The closed vocabulary, mirrored from pkg/datastore in DIME Terminal. A value
# outside it is not a feature request: the proxy dispatches on these strings
# and has no case for anything else, so the credential would be accepted and
# then never signed.
MODES = {"inject", "replace", "sign"}
# Mirrors datastore.ValidSignScheme, which mirrors the proxy's signer
# registry. A scheme missing here blocks an app the server would have loaded;
# one missing there takes a customer's whole proxy config out of service. The
# lists have to be added to together, and nothing enforces that from this
# repository — see README, "Where this disagrees with the server".
SCHEMES = {"hmac-sha256", "ecdsa-p256", "stark", "secp256k1"}
ENCODINGS = {"hex", "base64", "felt-pair"}
# datastore.ValidKeyEncoding. How the stored key is READ before signing with it,
# as distinct from `encoding`, which is how the signature is written back. Absent
# means raw, so the empty string is valid and means the same thing.
KEY_ENCODINGS = {"", "raw", "base64"}
# datastore.checkSignShape: only the MAC scheme reads a key encoding. The others
# sign with a key whose bytes are not a question.
KEY_ENCODING_SCHEME = "hmac-sha256"

# datastore.MaxAllowedRoutes and MaxAllowedHosts. Both are set where the
# number stops being possible for a key that belongs to one venue rather than
# where it stops being convenient: routes only ever NARROW a credential, so a
# limit on them caps how precisely an owner may scope their own key.
MAX_ROUTES = 1000
MAX_HOSTS = 100

# Every key the consumer's structs declare. Its YAML decoder runs with
# KnownFields(true), so anything outside these refuses the app outright — a
# misspelled key silently means the default, and every default is WIDER than
# what the author wrote.
KNOWN = {
    "manifest": {
        "schema_version", "id", "version", "name", "blurb", "description",
        "access", "maturity", "default_install",
        "environments", "exclusive_credential_types", "credential_types",
        "developer", "developer_url", "tint", "icon", "mcp_server",
    },
    "icon": {"path", "view_box"},
    "environment": {"id", "label", "host"},
    "credential_type": {
        "id", "label", "slug", "secret_label", "detail_fields", "routes",
        "delivery", "summary",
    },
    "detail_field": {"key", "label", "placeholder"},
    "delivery": {
        "mode", "header", "query_param", "formatter", "scheme", "encoding",
        "key_encoding", "match_headers", "match_body", "match_path",
        "match_query",
    },
    "route": {"path", "methods"},
    "mcp_server": {"command", "args", "tools", "credential_types", "environments"},
    "mcp_credential_type": {"type", "env"},
    "mcp_environment": {"id", "env"},
}

# Mirrored from pkg/apps: the mcp_server env rules. The sidecar sets the
# reserved names itself, so a manifest may not.
MCP_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
MCP_RESERVED_ENV = {
    "PATH", "HOME", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE", "AWS_CA_BUNDLE", "GIT_SSL_CAINFO", "UV_CACHE_DIR", "UV_CONSTRAINT",
}
MCP_TEMPLATE_RE = re.compile(r"\$\{(?:placeholder|label|detail\.([a-z0-9_]+))\}")
# Only inject has no placeholder: the secret goes out as itself.
MODES_WITHOUT_PLACEHOLDER = {"inject"}

# Which delivery fields each mode actually uses. The consumer canonicalises a
# delivery and refuses any difference from what was declared, so a field its
# mode does not read is not ignored — it refuses the app.
DELIVERY_FIELDS = {
    "inject": {"mode", "header", "query_param", "formatter"},
    "replace": {"mode", "match_headers", "match_body", "match_path", "match_query"},
    "sign": {"mode", "scheme", "encoding", "key_encoding", "match_headers",
             "match_body", "match_path", "match_query"},
}

# A known key whose value is the wrong kind fails the same way an unknown key
# does. PyYAML reads bare y and n as strings where yaml.v3 reads booleans, so
# this is stricter by two values, pinned in the divergence test.
BOOLEAN_KEYS = ("exclusive_credential_types", "default_install")

# The manifest format this checker understands, matching the consumer's.
SCHEMA_VERSION = 1

# Mirrors pkg/apps: MaxSkillFileBytes, MaxSkillFiles, MaxSkillsPerApp.
# MAX_FILES is per skill, so an app's ceiling is the product of the two.
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 32
MAX_SKILLS = 8


def check_links(failures: list[str]) -> None:
    """Refuse a symlink anywhere under apps/.

    The consumer refuses the whole archive on one, so a link here does not
    break one app — it takes the catalogue offline for every deployment until
    it is removed.
    """
    for p in APPS.rglob("*"):
        if p.is_symlink():
            failures.append(f"{p}: is a symlink; this repo holds only regular files")


def check_known(man: str, where: str, row: object, kind: str, failures: list[str]) -> None:
    """No key the consumer's decoder would refuse."""
    if not isinstance(row, dict):
        return
    for key in sorted(set(row) - KNOWN[kind]):
        failures.append(
            f"{man}: {where}.{key} is not a field the consumer knows — its decoder "
            "runs with KnownFields, so an unknown key refuses the whole app"
        )


def require(man: str, where: str, row: object, fields: tuple[str, ...],
            failures: list[str]) -> dict:
    """Every field present and non-empty on a mapping the consumer requires."""
    if not isinstance(row, dict):
        failures.append(f"{man}: {where} is not a mapping")
        return {}
    for field in fields:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            failures.append(
                f"{man}: {where}.{field} is required — the consumer refuses the app without it"
            )
    return row


def check_unique(man: str, where: str, values: list, failures: list[str]) -> None:
    seen = set()
    for i, v in enumerate(values):
        if v is None:
            continue
        if v in seen:
            failures.append(f"{man}: {where}[{i}] {v!r} is listed twice")
        seen.add(v)


def check_environments(man: str, envs: object, failures: list[str]) -> None:
    if not isinstance(envs, list) or not envs:
        failures.append(f"{man}: at least one environment is required")
        return
    # Each environment is a host a credential may be scoped to, and the server
    # caps how many one credential carries.
    if len(envs) > MAX_HOSTS:
        failures.append(
            f"{man}: {len(envs)} environments, and a credential may name {MAX_HOSTS} hosts"
        )
    for i, env in enumerate(envs):
        check_known(man, f"environments[{i}]", env, "environment", failures)
        row = require(man, f"environments[{i}]", env, ("id", "label", "host"), failures)
        if not row:
            continue
        eid, host = row.get("id"), row.get("host")
        if isinstance(eid, str) and not NAME_RE.match(eid):
            failures.append(f"{man}: environments[{i}].id {eid!r} must be lowercase letters, digits and hyphens")
        if isinstance(host, str) and host != host.lower():
            failures.append(f"{man}: environments[{i}].host {host!r} must be lowercase — the proxy matches case-sensitively")
        elif isinstance(host, str) and not HOST_RE.match(host):
            failures.append(
                f"{man}: environments[{i}].host {host!r} is not a bare hostname — no scheme, "
                "port, path or underscore, and it must carry a dot"
            )
    check_unique(man, "environments.id", [e.get("id") for e in envs if isinstance(e, dict)], failures)
    check_unique(man, "environments.host", [e.get("host") for e in envs if isinstance(e, dict)], failures)


def check_credential_types(man: str, types: object, failures: list[str]) -> None:
    if not isinstance(types, list) or not types:
        failures.append(f"{man}: at least one credential type is required")
        return
    for i, ct in enumerate(types):
        check_known(man, f"credential_types[{i}]", ct, "credential_type", failures)
        row = require(man, f"credential_types[{i}]", ct,
                      ("id", "label", "slug", "secret_label", "summary"), failures)
        if not row:
            continue
        where = f"credential_types[{i}]"
        cid, slug = row.get("id"), row.get("slug")
        if isinstance(cid, str) and not NAME_RE.match(cid):
            failures.append(f"{man}: {where}.id {cid!r} must be lowercase letters, digits and hyphens")
        if isinstance(slug, str) and not SLUG_RE.match(slug):
            failures.append(f"{man}: {where}.slug {slug!r} cannot be a credential label")

        routes = row.get("routes") or []
        for j, r in enumerate(routes):
            check_known(man, f"{where}.routes[{j}]", r, "route", failures)
            if not isinstance(r, dict):
                failures.append(f"{man}: {where}.routes[{j}] is not a mapping")
                continue
            path = r.get("path")
            if not isinstance(path, str) or not PATH_RE.match(path):
                failures.append(
                    f"{man}: {where}.routes[{j}].path {path!r} is not a path the API accepts — "
                    "it starts with / and carries no query string"
                )
            methods = r.get("methods") or []
            if not isinstance(methods, list):
                failures.append(f"{man}: {where}.routes[{j}].methods is not a list")
                continue
            if len(methods) > MAX_METHODS:
                failures.append(f"{man}: {where}.routes[{j}].methods lists {len(methods)}, limit {MAX_METHODS}")
            for m in methods:
                if m not in METHODS:
                    # Upper case is not a tidy-up: the proxy compares a method
                    # verbatim, so a lower-case entry is a rule that can never
                    # match — and a signing rule that never matches sends the
                    # request out UNSIGNED rather than refusing it.
                    failures.append(
                        f"{man}: {where}.routes[{j}].methods {m!r} is not one of {sorted(METHODS)}"
                    )
            check_unique(man, f"{where}.routes[{j}].methods", methods, failures)
        check_unique(man, f"{where}.routes.path",
                     [r.get("path") for r in routes if isinstance(r, dict)], failures)
        if len(routes) > MAX_ROUTES:
            failures.append(
                f"{man}: {where} narrows to {len(routes)} endpoints, and the API accepts {MAX_ROUTES}"
            )

        details = row.get("detail_fields") or []
        for j, f in enumerate(details):
            check_known(man, f"{where}.detail_fields[{j}]", f, "detail_field", failures)
            fld = require(man, f"{where}.detail_fields[{j}]", f, ("key", "label"), failures)
            key = fld.get("key") if fld else None
            if isinstance(key, str) and not DETAIL_KEY_RE.match(key):
                failures.append(
                    f"{man}: {where}.detail_fields[{j}].key {key!r} must be lowercase letters, "
                    "digits and inner underscores"
                )
        # A key is what the agent reads the value back by, so two fields
        # sharing one is a field the agent can never see.
        check_unique(man, f"{where}.detail_fields.key",
                     [d.get("key") for d in details if isinstance(d, dict)], failures)

        delivery = row.get("delivery") or {}
        if not isinstance(delivery, dict):
            failures.append(f"{man}: {where}.delivery is not a mapping")
            continue
        check_known(man, f"{where}.delivery", delivery, "delivery", failures)
        mode = delivery.get("mode")
        if mode not in MODES:
            failures.append(f"{man}: {where}.delivery.mode {mode!r} is not one of {sorted(MODES)}")
        else:
            # Present AND meaningful: a false match_body or an empty header is
            # what the consumer's canonical form would hold anyway.
            for field in sorted(set(delivery) - DELIVERY_FIELDS[mode]):
                if delivery.get(field) in (None, "", False, [], {}):
                    continue
                failures.append(
                    f"{man}: {where}.delivery.{field} is not used by mode {mode!r}, and the "
                    "consumer refuses a delivery that declares more than it canonicalises to"
                )
            if mode == "inject":
                has_header = bool(str(delivery.get("header") or "").strip())
                has_param = bool(str(delivery.get("query_param") or "").strip())
                if has_header == has_param:
                    failures.append(
                        f"{man}: {where}.delivery must name exactly one of header or query_param "
                        "for mode 'inject' — that is where the secret goes"
                    )
        if mode == "sign":
            if delivery.get("scheme") not in SCHEMES:
                failures.append(
                    f"{man}: {where}.delivery.scheme {delivery.get('scheme')!r} is not one of {sorted(SCHEMES)}"
                )
            if delivery.get("encoding") not in ENCODINGS:
                failures.append(
                    f"{man}: {where}.delivery.encoding {delivery.get('encoding')!r} is not one of {sorted(ENCODINGS)}"
                )
            key_encoding = as_text(delivery.get("key_encoding")) or ""
            if key_encoding not in KEY_ENCODINGS:
                failures.append(
                    f"{man}: {where}.delivery.key_encoding {key_encoding!r} is not one of "
                    f"{sorted(k for k in KEY_ENCODINGS if k)}"
                )
            elif key_encoding and delivery.get("scheme") != KEY_ENCODING_SCHEME:
                failures.append(
                    f"{man}: {where}.delivery.key_encoding is only read for "
                    f"{KEY_ENCODING_SCHEME}, and the scheme is "
                    f"{delivery.get('scheme')!r}"
                )
    check_unique(man, "credential_types.id", [c.get("id") for c in types if isinstance(c, dict)], failures)
    check_unique(man, "credential_types.slug", [c.get("slug") for c in types if isinstance(c, dict)], failures)


def check_mcp_env(man: str, where: str, env: object, failures: list[str]) -> dict[str, str]:
    """Names well formed and not reserved, values strings. Returns the readable pairs."""
    if env is None:
        return {}
    if not isinstance(env, dict):
        failures.append(f"{man}: {where} is not a mapping")
        return {}
    pairs = {}
    for name, value in env.items():
        if not isinstance(name, str) or not MCP_ENV_NAME_RE.fullmatch(name):
            failures.append(
                f"{man}: {where}.{name} is not an environment variable name "
                "(upper case letters, digits and underscores, at most 64)"
            )
            continue
        if name in MCP_RESERVED_ENV:
            failures.append(f"{man}: {where}.{name} is set by the sidecar and an app may not set it")
            continue
        text = as_text(value)
        if text is None:
            failures.append(f"{man}: {where}.{name} must be a string")
            continue
        if any(c in text for c in "\x00\n\r"):
            failures.append(f"{man}: {where}.{name} may not contain a NUL or a line break")
            continue
        pairs[name] = text
    return pairs


def check_mcp_list(man: str, where: str, rows: object, kind: str, key: str,
                   allowed: set[str], failures: list[str]) -> list[tuple[str, dict[str, str]]]:
    """One of mcp_server's two lists: each row names an app id once, plus its env."""
    if rows is None:
        return []
    if not isinstance(rows, list):
        failures.append(f"{man}: {where} is not a list")
        return []
    out = []
    for i, row in enumerate(rows):
        check_known(man, f"{where}[{i}]", row, kind, failures)
        if not isinstance(row, dict):
            failures.append(f"{man}: {where}[{i}] is not a mapping")
            continue
        ref = row.get(key) if isinstance(row.get(key), str) else None
        if ref not in allowed:
            failures.append(f"{man}: {where}[{i}].{key} {ref!r} is not one of this app's {sorted(allowed)}")
        out.append((ref, check_mcp_env(man, f"{where}[{i}].env", row.get("env"), failures)))
    check_unique(man, f"{where}.{key}", [ref for ref, _ in out], failures)
    return out


def check_mcp_server(man: str, doc: dict, failures: list[str]) -> None:
    """The optional MCP server an app runs, as pkg/apps validates it."""
    mcp = doc.get("mcp_server")
    if mcp is None:
        return
    if not isinstance(mcp, dict):
        failures.append(f"{man}: mcp_server is not a mapping")
        return
    check_known(man, "mcp_server", mcp, "mcp_server", failures)
    if not (as_text(mcp.get("command")) or "").strip():
        failures.append(f"{man}: mcp_server.command is required")

    args = mcp.get("args")
    if args is not None and (
        not isinstance(args, list) or any(as_text(a) is None for a in args)
    ):
        failures.append(f"{man}: mcp_server.args must be a list of strings")

    tools = mcp.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            failures.append(f"{man}: mcp_server.tools is not a list")
        elif not tools:
            failures.append(f"{man}: mcp_server.tools is empty: omit it to expose every tool")
        else:
            for i, t in enumerate(tools):
                if not (as_text(t) or "").strip():
                    failures.append(f"{man}: mcp_server.tools[{i}] must be a non-empty tool name")
            check_unique(man, "mcp_server.tools", [as_text(t) for t in tools], failures)

    types = {c.get("id"): c for c in doc.get("credential_types") or [] if isinstance(c, dict)}
    for ref, env in check_mcp_list(man, "mcp_server.credential_types", mcp.get("credential_types"),
                                   "mcp_credential_type", "type", set(types), failures):
        ct = types.get(ref) or {}
        delivery = ct.get("delivery") if isinstance(ct.get("delivery"), dict) else {}
        details = {d.get("key") for d in ct.get("detail_fields") or [] if isinstance(d, dict)}
        for name, value in env.items():
            where = f"mcp_server.credential_types[type={ref}].env.{name}"
            for m in MCP_TEMPLATE_RE.finditer(value):
                key = m.group(1)
                if key is not None and key not in details:
                    failures.append(f"{man}: {where} names ${{detail.{key}}}, and {ref!r} has no such detail field")
                if m.group(0) == "${placeholder}" and delivery.get("mode") in MODES_WITHOUT_PLACEHOLDER:
                    failures.append(
                        f"{man}: {where} uses ${{placeholder}}, and mode {delivery.get('mode')!r} has no placeholder"
                    )
            if "$" in MCP_TEMPLATE_RE.sub("", value):
                failures.append(
                    f"{man}: {where} {value!r} has a $ outside ${{placeholder}}, ${{label}} "
                    "and ${detail.<key>}"
                )

    env_ids = {e.get("id") for e in doc.get("environments") or [] if isinstance(e, dict)}
    for ref, env in check_mcp_list(man, "mcp_server.environments", mcp.get("environments"),
                                   "mcp_environment", "id", env_ids, failures):
        for name, value in env.items():
            if "$" in value:
                failures.append(
                    f"{man}: mcp_server.environments[id={ref}].env.{name} is a literal; "
                    "templates belong in credential_types"
                )


def as_text(value: object) -> str | None:
    """The string the consumer will see for a field it declares as a string.

    YAML is typed and Go's decoder coerces: `path: 0` arrives here as the int
    0 and arrives there as "0". Comparing Python truthiness instead of that
    string is how the two disagree — 0 is falsy here and non-empty there. None
    for a value Go could not decode into a string at all, which refuses the
    app on its side too.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


# KNOWN LIMIT, left in rather than fixed. as_text round-trips a numeric
# through Python's parsed value, and the consumer never parses it at all —
# yaml.v3 keeps the literal text when the target field is a string. They
# differ only where parsing loses something, which in practice means a float
# that overflows: 1.0e+400 is "inf" here and "1.0e+400" there. Closing it
# means reading the scalar's raw text, which needs a custom loader, and
# nothing anyone would write as an icon path or a colour reaches it.


ACCESS_VALUES = ("read-only", "read-write")
MATURITY_VALUES = ("stable", "beta")


def check_scope(man: str, doc: dict, failures: list[str]) -> None:
    """The two scope axes, REQUIRED HERE though optional in the consumer.

    The consumer defaults an absent value so that an app written before these
    existed keeps working. That is a compatibility rule, not an invitation:
    in this store every app states its own scope, because the default is the
    permissive one and an author who omitted it by accident would be
    publishing a writable, stable-looking app without having said so.
    """
    for key, allowed in (("access", ACCESS_VALUES), ("maturity", MATURITY_VALUES)):
        value = as_text(doc.get(key))
        if not value:
            failures.append(
                f"{man}: {key} is required — say which of {', '.join(allowed)} this app is"
            )
        elif value not in allowed:
            failures.append(
                f"{man}: {key} {value!r} is not one of {', '.join(allowed)}"
            )


def check_presentation(man: str, doc: dict, failures: list[str]) -> None:
    """The optional branding, which is optional but not unchecked.

    Half an icon is refused rather than defaulted: a path with no view box
    draws at the wrong scale and a view box with no path draws nothing, which
    both look deliberate.
    """
    # Empty is absent, because that is how the consumer reads it: its checks
    # are `!= ""`, so a manifest with `tint: ""` loads there and refusing it
    # here would block one that is fine. Same reasoning as the empty icon.
    tint = as_text(doc.get("tint"))
    if tint is None or (tint != "" and not TINT_RE.match(tint)):
        failures.append(f"{man}: tint {doc.get('tint')!r} must be a six-digit hex colour like #5f74ff")

    url = as_text(doc.get("developer_url"))
    if url is None:
        failures.append(f"{man}: developer_url {doc.get('developer_url')!r} must be an https URL")
    elif url != "":
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            failures.append(f"{man}: developer_url {url!r} must be an https URL")
        elif not (as_text(doc.get("developer")) or "").strip():
            failures.append(f"{man}: developer_url without developer: the link needs something to sit on")

    icon = doc.get("icon")
    if icon is None:
        return
    if not isinstance(icon, dict):
        failures.append(f"{man}: icon is not a mapping")
        return
    check_known(man, "icon", icon, "icon", failures)
    path, box = as_text(icon.get("path")), as_text(icon.get("view_box"))
    if path is None or box is None:
        failures.append(f"{man}: icon.path and icon.view_box must be strings")
        return
    # Neither is "no icon", which the consumer accepts. Only one of the two is
    # half an icon.
    if path == "" and box == "":
        return
    if path == "" or box == "":
        failures.append(f"{man}: icon needs both path and view_box, or neither")
        return
    if not ICON_PATH_RE.match(path):
        failures.append(
            f"{man}: icon.path may hold only SVG path commands, numbers and separators — "
            "it is geometry, not a document"
        )
    elif len(path) > MAX_ICON_PATH:
        failures.append(f"{man}: icon.path is {len(path)} bytes, limit {MAX_ICON_PATH}")
    if not VIEW_BOX_RE.match(box):
        failures.append(f"{man}: icon.view_box {box!r} must be four numbers")


def check_manifest(app: str, text: str, failures: list[str]) -> str:
    """Check one manifest, and return the version it declares."""
    man = f"{APPS}/{app}/{MANIFEST}"
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        failures.append(f"{man}: is not readable YAML: {exc}")
        return ""
    if not isinstance(doc, dict):
        failures.append(f"{man}: is not a mapping")
        return ""

    check_known(man, "manifest", doc, "manifest", failures)

    if doc.get("schema_version") != SCHEMA_VERSION:
        failures.append(f"{man}: schema_version is {doc.get('schema_version')!r}, this checker reads {SCHEMA_VERSION}")

    declared_id = doc.get("id")
    if declared_id != app:
        failures.append(f"{man}: id is {declared_id!r}, directory is {app!r}")
    elif not NAME_RE.match(declared_id):
        failures.append(f"{man}: id {declared_id!r} must be lowercase letters, digits and hyphens")

    require(man, "manifest", doc, ("version", "name", "blurb", "description"), failures)

    if "skill" in doc:
        failures.append(
            f"{man}: manifests do not name skills. The directory beside this file is the content"
        )

    for key in BOOLEAN_KEYS:
        if key in doc and doc[key] is not None and not isinstance(doc[key], bool):
            failures.append(
                f"{man}: {key} is {doc[key]!r}, and the consumer decodes it into a "
                "bool. Use true or false"
            )

    check_presentation(man, doc, failures)
    check_scope(man, doc, failures)
    check_environments(man, doc.get("environments"), failures)
    check_credential_types(man, doc.get("credential_types"), failures)
    check_mcp_server(man, doc, failures)
    return as_text(doc.get("version")) or ""


def frontmatter(md: str) -> dict[str, str]:
    if not md.startswith("---\n"):
        return {}
    block = md[4:].split("\n---", 1)
    if len(block) < 2:
        return {}
    try:
        got = yaml.safe_load(block[0])
    except yaml.YAMLError:
        return {}
    return got if isinstance(got, dict) else {}


# The lines every shipped client repeats. They are the proxy's protocol, not the
# venue's, so a copy that drifts is a bug we get to find once per app.
CLIENT_PREAMBLE = """const all = Object.keys(process.env).filter(k => (process.env[k] || '').startsWith('sign-{venue}'));
const V = process.env.{VENUE}_CRED || all[0];
if (!V) {{ console.error('no {Venue} sign- credential in the environment'); process.exit(2); }}
if (all.length > 1 && !process.env.{VENUE}_CRED) {{
  console.error('several {Venue} credentials: ' + all.join(', ') + ' \u2014 re-run with {VENUE}_CRED=<the one you want>');
  process.exit(2);
}}
const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');"""


def check_client_preamble(app: str, files: list, failures: list[str]) -> None:
    """A shipped client opens with the proxy protocol, spelled one way."""
    for f in files:
        if f.suffix != ".mjs" or f.parent.name != "scripts":
            continue
        want = CLIENT_PREAMBLE.format(
            venue=app, VENUE=app.upper(), Venue=app.capitalize()
        ).split("\n")
        got = f.read_text().split("\n")
        if not any(l.startswith("const all = Object.keys") for l in got):
            failures.append(f"{f}: no credential preamble; a shipped client starts with one")
            continue
        at = next(i for i, l in enumerate(got) if l.startswith("const all = Object.keys"))
        block = got[at:at + len(want)]
        if block != want:
            for i, (a, b) in enumerate(zip(block, want)):
                if a != b:
                    failures.append(
                        f"{f}:{at + i + 1}: this line is the proxy protocol and every "
                        f"client spells it the same way.\n     got:  {a}\n     want: {b}"
                    )
                    break


ERRATA_RE = re.compile(r"errata-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\.md")


def check_errata_version(app: str, version: str, files: list, failures: list[str]) -> None:
    """An errata filename names the version that ships it, or it never expires."""
    for f in files:
        if f.suffix != ".md":
            continue
        for m in ERRATA_RE.finditer(f.read_text()):
            if m.group("version") != version:
                failures.append(
                    f"{f}: names {m.group(0)} while {app} is {version}. The version in "
                    "that name is the whole expiry, so a mismatch means an agent reads "
                    "a note about a bug you have already fixed."
                )
def check_helper_versions(
    app: str, version: str, skill: str, files: list[Path], failures: list[str]
) -> None:
    """Every helper path a skill names carries this app's version.

    One failure per distinct wrong path, however many times the file repeats it.
    A skill names its helper on every call it demonstrates, so reporting each
    occurrence buries the other apps' output under one mistake.
    """
    for f in files:
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        misplaced: set[str] = set()
        stale: set[str] = set()
        for m in HELPER_RE.finditer(text):
            if m.group("app") != app or m.group("skill") != skill:
                misplaced.add(f"{m.group('app')}/{m.group('skill')}")
            elif m.group("version") != version:
                stale.add(m.group("version"))
        for where in sorted(misplaced):
            failures.append(
                f"{f}: names a helper under tools/{where}/, and this is {app}/{skill}"
            )
        for got in sorted(stale):
            failures.append(
                f"{f}: the helper is named for version {got!r} and {app}/{MANIFEST} "
                f"says {version!r}. A bump the filename does not follow never "
                "invalidates the agent's cached copy"
            )


def main() -> int:
    failures: list[str] = []
    check_links(failures)
    apps = sorted(p for p in APPS.iterdir() if p.is_dir()) if APPS.is_dir() else []
    if not apps:
        print("no apps found", file=sys.stderr)
        return 1

    claims: dict[str, str] = {}
    for app in apps:
        man = app / MANIFEST
        if not man.is_file():
            failures.append(f"{app}: no {MANIFEST}")
            continue
        version = check_manifest(app.name, man.read_text(), failures)

        skills_dir = app / "skills"
        on_disk = (
            sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
            if skills_dir.is_dir()
            else []
        )
        if not on_disk:
            failures.append(
                f"{app}: no skills/ — an app must ship at least one, or it is indistinguishable "
                "from one whose files went missing, and that reads as an uninstall"
            )
        if len(on_disk) > MAX_SKILLS:
            failures.append(
                f"{app}: {len(on_disk)} skills ({', '.join(on_disk)}), limit {MAX_SKILLS}"
            )

        for name in on_disk:
            if name in claims:
                failures.append(
                    f"{app}: skill name {name!r} is already claimed by {claims[name]!r} — "
                    "openclaw resolves a collision by precedence, so one of them would "
                    "silently never load"
                )
            claims[name] = app.name
            if not NAME_RE.match(name):
                failures.append(f"{app}: {name!r} is not a usable skill name")
            d = skills_dir / name
            entry = d / ENTRYPOINT
            if not entry.is_file():
                failures.append(f"{d}: no {ENTRYPOINT}")
                continue
            fm = frontmatter(entry.read_text())
            if fm.get("name") != name:
                failures.append(
                    f"{entry}: frontmatter name is {fm.get('name')!r}, directory is {name!r}"
                )
            if not str(fm.get("description") or "").strip():
                failures.append(f"{entry}: frontmatter has no description")

            files = [p for p in d.rglob("*") if p.is_file()]
            check_client_preamble(app.name, files, failures)
            if version:
                check_errata_version(app.name, version, files, failures)
                check_helper_versions(app.name, version, name, files, failures)
            if len(files) > MAX_FILES:
                failures.append(f"{d}: {len(files)} files, limit {MAX_FILES}")
            for f in files:
                if f.stat().st_size > MAX_FILE_BYTES:
                    failures.append(f"{f}: {f.stat().st_size} bytes, limit {MAX_FILE_BYTES}")

        print(f"{app.name}: {', '.join(on_disk) if on_disk else 'no skills'}")

    if failures:
        print("\nStructure check failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\n{len(apps)} app(s) checked — all good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
