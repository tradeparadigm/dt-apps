#!/usr/bin/env python3
"""Refuse a changed skill whose app keeps its version.

`app_versions` is keyed (app_id, version) and written ON CONFLICT DO NOTHING,
so the first files stored under a version string are the ones kept. Edit a
skill, leave `version:` alone, and nobody who already installed the app ever
sees the change. Reinstalling republishes the pinned row.

Compares the merge base against HEAD. BASE_SHA and HEAD_SHA come from CI;
locally they default to origin/main and HEAD.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

VERSION = re.compile(r"^version:\s*(.+?)\s*$", re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=True).stdout


def version_at(rev: str, path: str) -> str | None:
    try:
        got = git("show", f"{rev}:{path}")
    except subprocess.CalledProcessError:
        return None
    m = VERSION.search(got)
    return m.group(1) if m else None


def main() -> int:
    base = os.environ.get("BASE_SHA") or "origin/main"
    head = os.environ.get("HEAD_SHA") or "HEAD"
    try:
        merge_base = git("merge-base", base, head).strip()
    except subprocess.CalledProcessError as e:
        print(f"cannot find the merge base of {base} and {head}: {e}", file=sys.stderr)
        return 2

    changed = [p for p in git("diff", "--name-only", merge_base, head).splitlines()
               if p.startswith("apps/")]
    touched: dict[str, list[str]] = {}
    for p in changed:
        parts = Path(p).parts
        if len(parts) >= 2:
            touched.setdefault(parts[1], []).append(p)

    failures = []
    for app, files in sorted(touched.items()):
        manifest = f"apps/{app}/app.yaml"
        # Only the manifest changed, or the app is new: nothing to enforce.
        if files == [manifest] or version_at(merge_base, manifest) is None:
            continue
        before = version_at(merge_base, manifest)
        after = version_at(head, manifest)
        if before == after:
            edited = ", ".join(sorted(f for f in files if f != manifest)[:4])
            failures.append(
                f"  {app}: version is still {after} and these changed: {edited}")

    if failures:
        print("A changed app must change its version:\n" + "\n".join(failures))
        print("\nEdit apps/<id>/app.yaml. Anyone who installed the app reads the "
              "version it was pinned at, so an unbumped edit never reaches them.")
        return 1

    print(f"version bump check: {len(touched)} app(s) changed, all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
