#!/usr/bin/env python3
"""Check every app in this repo is shaped the way DIME Terminal reads it.

The consumer refuses to start on any of these, which means a mistake here
fails someone else's deploy rather than this repo's CI. So they are checked
where they are made:

  * apps/<id>/skills.yaml exists, and its `id` matches the directory.
  * Every name in `skills:` has a directory beside it holding a SKILL.md.
  * Every skill directory is listed. An unlisted one is published by nobody
    and is the silent half of a rename.
  * A SKILL.md's frontmatter `name` equals its directory name, and it has a
    non-empty `description` — the consumer compares both.
  * Names are lowercase letters, digits and hyphens: they become path
    segments on an agent's disk.

Stdlib only, so the workflow is checkout plus one python3 invocation.

Exit 0 = all good, 1 = at least one violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APPS = Path("apps")
ENTRYPOINT = "SKILL.md"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# The consumer's own limits (pkg/apps: MaxSkillFileBytes, MaxSkillFiles).
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 32


def manifest_field(text: str, key: str) -> str | None:
    m = re.search(rf"^{key}\s*:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else None


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
    apps = sorted(p for p in APPS.iterdir() if p.is_dir()) if APPS.is_dir() else []
    if not apps:
        print("no apps found", file=sys.stderr)
        return 1

    for app in apps:
        man = app / "skills.yaml"
        if not man.is_file():
            failures.append(f"{app}: no skills.yaml")
            continue
        text = man.read_text()
        declared_id = manifest_field(text, "id")
        if declared_id != app.name:
            failures.append(f"{man}: id is {declared_id!r}, directory is {app.name!r}")

        listed = listed_skills(text)
        skills_dir = app / "skills"
        on_disk = sorted(p.name for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []

        for name in sorted(set(listed) - set(on_disk)):
            failures.append(f"{man}: lists {name!r}, which has no directory")
        for name in sorted(set(on_disk) - set(listed)):
            failures.append(f"{app}: {name}/ exists but skills.yaml does not list it")

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
