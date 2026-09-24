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
  * Hosts are lowercase. The proxy matches them case-sensitively, so an
    uppercase letter is a rule that can never fire.
  * A credential type names a delivery mode, and a signing scheme and encoding,
    that the proxy actually has a case for. A manifest may describe an app; it
    cannot invent a capability.
  * A credential type narrows itself to no more endpoints than the API accepts.
  * detail_fields carry a key the API can write into public metadata, and a
    label, because the label is what the enrolment form shows.
  * Nothing is a symlink. DIME Terminal reads this repo as a tarball and
    refuses a link outright, because a link is a file whose contents are a
    path: followed, it names something outside the tree; skipped, it empties a
    skill directory and the app loads as one that teaches nothing, which
    deletes its skill off every agent that installed it.
  * EXACTLY one skill per app. Not "at most one": the agent's publish path
    writes an app's files under <skills>/apps/<id>/ and discovers a skill by
    SKILL.md at that root, so a second has nowhere to go. Zero is refused for a
    different reason: a publish is a full replacement, an app contributing no
    files is dropped from the set, and so an app that ships no skill is
    byte-identical to one whose files did not survive the fetch — which
    deletes a working skill off every agent that installed it.
  * Every directory under skills/ holds a SKILL.md whose frontmatter `name`
    equals the directory, with a non-empty description.

The manifest names NO skills. The directory is the content, the id is the
join, and there is nothing for two files to disagree about.

Exit 0 = all good, 1 = at least one violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

APPS = Path("apps")
MANIFEST = "app.yaml"
ENTRYPOINT = "SKILL.md"

# Mirrored from pkg/apps: idPattern, slugPattern, detailKeyPattern.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
DETAIL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}[a-z0-9]$|^[a-z0-9]$")

# The closed vocabulary, mirrored from pkg/datastore in DIME Terminal. A value
# outside it is not a feature request: the proxy dispatches on these strings
# and has no case for anything else, so the credential would be accepted and
# then never signed.
MODES = {"inject", "replace", "sign"}
SCHEMES = {"hmac-sha256", "ecdsa-p256", "stark"}
ENCODINGS = {"hex", "base64", "felt-pair"}

# maxAllowedRoutesPerCredential in web-api/services/credentials.go. Routes
# SUBTRACT — more of them narrow a credential rather than widening it — so this
# is a readability and config-size bound, not a grant boundary. It reads 64
# from dime-terminal#233; until that lands the consumer still says 20 and
# binance is refused from the live catalogue, isolated to itself by design.
MAX_ROUTES = 64

# The manifest format this checker understands, matching the consumer's.
SCHEMA_VERSION = 1

# The consumer's own limits (pkg/apps: MaxSkillFileBytes, MaxSkillFiles).
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 32


def check_links(failures: list[str]) -> None:
    """Refuse a symlink anywhere under apps/.

    The consumer refuses the whole archive on one, so a link here does not
    break one app — it takes the catalogue offline for every deployment until
    it is removed.
    """
    for p in APPS.rglob("*"):
        if p.is_symlink():
            failures.append(f"{p}: is a symlink; this repo holds only regular files")


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
    for i, env in enumerate(envs):
        row = require(man, f"environments[{i}]", env, ("id", "label", "host"), failures)
        if not row:
            continue
        eid, host = row.get("id"), row.get("host")
        if isinstance(eid, str) and not NAME_RE.match(eid):
            failures.append(f"{man}: environments[{i}].id {eid!r} must be lowercase letters, digits and hyphens")
        if isinstance(host, str) and host != host.lower():
            failures.append(f"{man}: environments[{i}].host {host!r} must be lowercase — the proxy matches case-sensitively")
    check_unique(man, "environments.id", [e.get("id") for e in envs if isinstance(e, dict)], failures)
    check_unique(man, "environments.host", [e.get("host") for e in envs if isinstance(e, dict)], failures)


def check_credential_types(man: str, types: object, failures: list[str]) -> None:
    if not isinstance(types, list) or not types:
        failures.append(f"{man}: at least one credential type is required")
        return
    for i, ct in enumerate(types):
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
        if len(routes) > MAX_ROUTES:
            failures.append(
                f"{man}: {where} narrows to {len(routes)} endpoints, and the API accepts {MAX_ROUTES}"
            )

        for j, f in enumerate(row.get("detail_fields") or []):
            fld = require(man, f"{where}.detail_fields[{j}]", f, ("key", "label"), failures)
            key = fld.get("key") if fld else None
            if isinstance(key, str) and not DETAIL_KEY_RE.match(key):
                failures.append(
                    f"{man}: {where}.detail_fields[{j}].key {key!r} must be lowercase letters, "
                    "digits and inner underscores"
                )

        delivery = row.get("delivery") or {}
        if not isinstance(delivery, dict):
            failures.append(f"{man}: {where}.delivery is not a mapping")
            continue
        mode = delivery.get("mode")
        if mode not in MODES:
            failures.append(f"{man}: {where}.delivery.mode {mode!r} is not one of {sorted(MODES)}")
        if mode == "sign":
            if delivery.get("scheme") not in SCHEMES:
                failures.append(
                    f"{man}: {where}.delivery.scheme {delivery.get('scheme')!r} is not one of {sorted(SCHEMES)}"
                )
            if delivery.get("encoding") not in ENCODINGS:
                failures.append(
                    f"{man}: {where}.delivery.encoding {delivery.get('encoding')!r} is not one of {sorted(ENCODINGS)}"
                )
    check_unique(man, "credential_types.id", [c.get("id") for c in types if isinstance(c, dict)], failures)
    check_unique(man, "credential_types.slug", [c.get("slug") for c in types if isinstance(c, dict)], failures)


def check_manifest(app: str, text: str, failures: list[str]) -> None:
    man = f"{APPS}/{app}/{MANIFEST}"
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        failures.append(f"{man}: is not readable YAML: {exc}")
        return
    if not isinstance(doc, dict):
        failures.append(f"{man}: is not a mapping")
        return

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

    check_environments(man, doc.get("environments"), failures)
    check_credential_types(man, doc.get("credential_types"), failures)


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


def main() -> int:
    failures: list[str] = []
    check_links(failures)
    apps = sorted(p for p in APPS.iterdir() if p.is_dir()) if APPS.is_dir() else []
    if not apps:
        print("no apps found", file=sys.stderr)
        return 1

    for app in apps:
        man = app / MANIFEST
        if not man.is_file():
            failures.append(f"{app}: no {MANIFEST}")
            continue
        check_manifest(app.name, man.read_text(), failures)

        skills_dir = app / "skills"
        on_disk = (
            sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
            if skills_dir.is_dir()
            else []
        )
        if not on_disk:
            failures.append(
                f"{app}: no skills/ — an app must ship exactly one, or it is indistinguishable "
                "from one whose files went missing, and that reads as an uninstall"
            )
        if len(on_disk) > 1:
            failures.append(
                f"{app}: {len(on_disk)} skills ({', '.join(on_disk)}) — one per app until the agent can take nested paths"
            )

        for name in on_disk:
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
