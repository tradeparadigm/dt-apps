#!/usr/bin/env python3
"""Check every app in this repo is shaped the way DIME Terminal reads it.

THIS REPO IS THE STORE. DIME Terminal fetches it at run time, so a malformed
app here is not caught by anyone's build — it is dropped from a live
catalogue with a line in a log nobody reads. The checks that used to happen
at someone else's deploy therefore happen here, against the pull request that
made the mistake:

  * apps/<id>/app.yaml exists, and its `id` matches the directory.
  * The manifest carries what the catalogue needs: version, name, blurb,
    description, at least one environment and one credential type.
  * Every environment and credential type carries what the consumer requires
    of it, and their ids are unique. These used to be checked only on the
    reading side, so a manifest missing a secret_label or a summary went green
    here and was dropped from the LIVE catalogue on the next refresh — which is
    exactly the failure this repo exists to move left.
  * Hosts are lowercase. The proxy matches them case-sensitively, so an
    uppercase letter is a rule that can never fire.
  * A credential type names a delivery mode, and a signing scheme and
    encoding, that the proxy actually has a case for. A manifest may describe
    an app; it cannot invent a capability.
  * Ids and slugs match the patterns the API validates labels against, so a
    template cannot produce a credential nobody can enrol.
  * Every directory under skills/ holds a SKILL.md whose frontmatter `name`
    equals the directory, with a non-empty description.
  * Nothing is a symlink. DIME Terminal reads this repo as a tarball and
    refuses a link outright, because a link is a file whose contents are a
    path: followed, it names something outside the tree; skipped, it empties
    a skill directory and the app loads as one that teaches nothing, which
    deletes its skill off every agent that installed it.
  * EXACTLY one skill per app. Not "at most one": the agent's publish path writes an app's
    files under <skills>/apps/<id>/ and discovers a skill by SKILL.md at that
    root, so a second has nowhere to go yet. Zero is refused for a different
    reason: a publish is a full replacement, an app contributing no files is
    dropped from the set, and so an app that ships no skill is byte-identical
    to one whose files did not survive the fetch — which deletes a working
    skill off every agent that installed it.

The manifest names NO skills. The directory is the content, the id is the
join, and there is nothing for two files to disagree about.

Stdlib only, so the workflow is checkout plus one python3 invocation.

Exit 0 = all good, 1 = at least one violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APPS = Path("apps")
MANIFEST = "app.yaml"
ENTRYPOINT = "SKILL.md"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

# The closed vocabulary, mirrored from pkg/datastore in DIME Terminal. A value
# outside it is not a feature request: the proxy dispatches on these strings
# and has no case for anything else, so the credential would be accepted and
# then never signed.
MODES = {"inject", "replace", "sign"}
SCHEMES = {"hmac-sha256", "ecdsa-p256", "stark"}
ENCODINGS = {"hex", "base64", "felt-pair"}

# The manifest format this checker understands, matching the consumer's.
SCHEMA_VERSION = 1

# The consumer's own limits (pkg/apps: MaxSkillFileBytes, MaxSkillFiles).
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 32


def entries(text: str, section: str) -> list[dict[str, str]]:
    """Every mapping under a top-level `section:` list, as scalar key/value.

    Enough structure to check per-entry required fields, and no more: nested
    lists inside an entry (detail_fields, routes) are skipped rather than
    parsed, because nothing here needs to look inside them. Still not a YAML
    parser, for the reason block_values gives — this is a check on shape, and
    anything it cannot see is caught on the reading side.
    """
    out: list[dict[str, str]] = []
    depth: int | None = None
    inside = False
    skipping = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(rf"^{section}\s*:", line):
            inside = True
            continue
        if inside and not line[:1].isspace():
            break  # the next top-level key ends the section
        if not inside:
            continue

        indent = len(line) - len(line.lstrip())
        item = re.match(r"^(\s*)-\s+(\S.*)$", line)
        if item is not None and (depth is None or len(item.group(1)) == depth):
            depth = len(item.group(1))
            out.append({})
            skipping = False
            line, indent = item.group(1) + "  " + item.group(2), depth + 2

        if not out:
            continue
        kv = re.match(r"^\s*([a-z_]+)\s*:\s*(.*)$", line)
        if kv is None:
            continue
        # A key whose value is empty opens a nested block; skip until the
        # indentation comes back.
        if skipping and indent > skipping:
            continue
        skipping = False
        key, value = kv.group(1), kv.group(2).strip().strip("\"'")
        if value == "":
            skipping = indent
            continue
        out[-1][key] = value
    return out


def check_collection(man: str, kind: str, rows: list[dict[str, str]],
                     required: tuple[str, ...], unique: tuple[str, ...],
                     failures: list[str]) -> None:
    """Required fields and uniqueness for one list of mappings."""
    if not rows:
        failures.append(f"{man}: at least one {kind} is required")
        return
    seen: dict[str, set[str]] = {k: set() for k in unique}
    for i, row in enumerate(rows):
        for field in required:
            if not row.get(field):
                failures.append(
                    f"{man}: {kind}s[{i}].{field} is required — the consumer refuses the app without it"
                )
        for field in unique:
            value = row.get(field)
            if value is None:
                continue
            if value in seen[field]:
                failures.append(f"{man}: {kind}s[{i}].{field} {value!r} is listed twice")
            seen[field].add(value)


def manifest_field(text: str, key: str) -> str | None:
    m = re.search(rf"^{key}\s*:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else None


def block_values(text: str, key: str) -> list[str]:
    """Every `key: value` under any indentation, in order.

    Deliberately a scan and not a YAML parse: stdlib only, and this is a
    check on shape rather than a second implementation of the consumer's
    reader. Anything it cannot see is still caught there.
    """
    return [m.group(1).strip().strip("\"'") for m in re.finditer(rf"^\s*{key}\s*:\s*(\S.*)$", text, re.MULTILINE)]


def check_links(failures: list[str]) -> None:
    """Refuse a symlink anywhere under apps/.

    The consumer refuses the whole archive on one, so a link here does not
    break one app — it takes the catalogue offline for every deployment until
    it is removed.
    """
    for p in APPS.rglob("*"):
        if p.is_symlink():
            failures.append(f"{p}: is a symlink; this repo holds only regular files")


def check_manifest(app: str, text: str, failures: list[str]) -> None:
    man = f"{APPS}/{app}/{MANIFEST}"

    schema = manifest_field(text, "schema_version")
    if schema != str(SCHEMA_VERSION):
        failures.append(f"{man}: schema_version is {schema!r}, this checker reads {SCHEMA_VERSION}")

    declared_id = manifest_field(text, "id")
    if declared_id != app:
        failures.append(f"{man}: id is {declared_id!r}, directory is {app!r}")
    elif not NAME_RE.match(declared_id):
        failures.append(f"{man}: id {declared_id!r} must be lowercase letters, digits and hyphens")

    for field in ("version", "name", "blurb", "description"):
        if not manifest_field(text, field):
            failures.append(f"{man}: {field} is required — the catalogue renders it")

    if "skill:" in text:
        failures.append(
            f"{man}: manifests do not name skills. The directory beside this file is the content"
        )

    # The consumer's App.validate refuses an app missing any of these, and a
    # dropped app is a line in a log nobody reads — so they are checked in the
    # pull request that makes the mistake.
    check_collection(man, "environment", entries(text, "environments"),
                     required=("id", "label", "host"), unique=("id", "host"),
                     failures=failures)
    check_collection(man, "credential_type", entries(text, "credential_types"),
                     required=("id", "label", "slug", "secret_label", "summary"),
                     unique=("id", "slug"), failures=failures)

    hosts = block_values(text, "host")
    if not hosts:
        failures.append(f"{man}: at least one environment is required")
    for host in hosts:
        if host != host.lower():
            failures.append(f"{man}: host {host!r} must be lowercase — the proxy matches case-sensitively")

    modes = block_values(text, "mode")
    if not modes:
        failures.append(f"{man}: at least one credential type is required")
    for mode in modes:
        if mode not in MODES:
            failures.append(f"{man}: delivery mode {mode!r} is not one of {sorted(MODES)}")
    for scheme in block_values(text, "scheme"):
        if scheme not in SCHEMES:
            failures.append(f"{man}: signing scheme {scheme!r} is not one of {sorted(SCHEMES)}")
    for enc in block_values(text, "encoding"):
        if enc not in ENCODINGS:
            failures.append(f"{man}: signature encoding {enc!r} is not one of {sorted(ENCODINGS)}")
    for slug in block_values(text, "slug"):
        if not SLUG_RE.match(slug):
            failures.append(f"{man}: slug {slug!r} cannot be a credential label")


def listed_skills(text: str) -> list[str]:
    body = text.split("skills:", 1)
    if len(body) < 2:
        return []
    out = []
    for line in body[1].splitlines():
        if line.startswith("  - ") or line.startswith("- "):
            out.append(line.split("-", 1)[1].strip())
        elif line.strip() and not line.startswith(" "):
            break
    return out


def frontmatter(md: str) -> dict[str, str]:
    if not md.startswith("---\n"):
        return {}
    block = md[4:].split("\n---", 1)
    if len(block) < 2:
        return {}
    out = {}
    for line in block[0].splitlines():
        m = re.match(r"^([a-z_]+)\s*:\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


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
            # The agent's publish path writes one app's files under
            # <skills>/apps/<id>/ and finds a skill by SKILL.md at that root.
            # A second skill is not dropped quietly there; the app is refused
            # whole, so it is refused here too.
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
            if not fm.get("description"):
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
