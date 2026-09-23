#!/usr/bin/env python3
"""Validate app version bumps in a pull request.

For every app whose content changed, compare ``version:`` in
``apps/<id>/skills.yaml`` at the merge base against the version at HEAD:

  * Content changed and the version did NOT move -> FAIL. DIME Terminal
    records the version a user installed and compares it to offer an update,
    so content that ships without a bump reaches nobody and looks current.
  * A change must be a single, clean, forward step of **exactly one** in
    exactly one component:
        major:  X.Y.Z -> (X+1).0.0
        minor:  X.Y.Z -> X.(Y+1).0
        patch:  X.Y.Z -> X.Y.(Z+1)
  * A jump of two or more, a multi-component change, or a decrease is
    rejected.

Versions may be written with two components (``"1.5"``) or three; they are
normalised to three (padding with zeros) before comparison.

Base and head revisions come from ``BASE_SHA`` / ``HEAD_SHA`` when set (as in
CI), otherwise ``origin/main`` and ``HEAD``.

Exit code 0 = all good, 1 = at least one violation, 2 = the tool itself failed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

APPS_DIR = "apps"
MANIFEST = "skills.yaml"

# A TOP-LEVEL `version:` line, unindented — the manifest is plain YAML with no
# frontmatter, so there is no nesting to scope the search to.
_VERSION_RE = re.compile(r'^version\s*:\s*["\']?([^"\'\s#]+)', re.MULTILINE)


def run(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def git_show(rev: str, path: str) -> str | None:
    """Contents of *path* at *rev*, or None when it does not exist there."""
    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def parse_version(content: str | None) -> str | None:
    if not content:
        return None
    match = _VERSION_RE.search(content)
    return match.group(1).strip() if match else None


def normalize(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"expected 1-3 numeric components, got '{version}'")
    nums = []
    for p in parts:
        if not p.isdigit():
            raise ValueError(f"non-numeric component in '{version}'")
        nums.append(int(p))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def classify(old: tuple[int, int, int], new: tuple[int, int, int]) -> str:
    """'same', 'major', 'minor', 'patch', or an 'ERROR: …' explanation."""
    if new == old:
        return "same"
    om, omi, op = old
    if new == (om + 1, 0, 0):
        return "major"
    if new == (om, omi + 1, 0):
        return "minor"
    if new == (om, omi, op + 1):
        return "patch"
    if new < old:
        return "ERROR: version decreased"
    return (
        "ERROR: version must increase by exactly one component "
        "(major, minor, or patch) at a time"
    )


def changed_apps(base: str, head: str) -> list[str]:
    """App ids with any changed file under apps/<id>/, deletions included.

    Deletions count: removing a skill is a content change a user must be
    offered. Three-dot so the comparison is against the merge base.
    """
    out = run("diff", "--name-only", f"{base}...{head}", "--", APPS_DIR)
    ids = set()
    for line in out.splitlines():
        parts = Path(line).parts
        if len(parts) >= 2 and parts[0] == APPS_DIR:
            ids.add(parts[1])
    return sorted(ids)


def remaining_files(rev: str, app: str) -> bool:
    """Whether apps/<app>/ still holds anything at *rev*."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", rev, f"{APPS_DIR}/{app}/"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def main() -> int:
    base = os.environ.get("BASE_SHA", "origin/main")
    head = os.environ.get("HEAD_SHA", "HEAD")

    try:
        merge_base = run("merge-base", base, head).strip()
    except subprocess.CalledProcessError as exc:
        print(f"error: no merge base for {base} and {head}", file=sys.stderr)
        print(exc.stderr, file=sys.stderr)
        return 2

    try:
        apps = changed_apps(base, head)
    except subprocess.CalledProcessError as exc:
        print(f"error: git diff failed\n{exc.stderr}", file=sys.stderr)
        return 2

    if not apps:
        print("No app content changed in this PR — version check skipped.")
        return 0

    failures: list[str] = []

    for app in apps:
        path = f"{APPS_DIR}/{app}/{MANIFEST}"
        new_content = git_show(head, path)
        old_content = git_show(merge_base, path)

        if new_content is None:
            if old_content is None:
                # Changed files under an app with no manifest on either side.
                failures.append(f"{app}: no {MANIFEST}")
            elif remaining_files(head, app):
                # The manifest went but the skills did not, so the app still
                # publishes content that nothing versions.
                failures.append(
                    f"{app}: {MANIFEST} was deleted while files under "
                    f"{APPS_DIR}/{app}/ remain"
                )
            else:
                print(f"{app}: removed.")
            continue

        new_version = parse_version(new_content)
        if new_version is None:
            failures.append(f"{app}: {MANIFEST} declares no version")
            continue

        if old_content is None:
            print(f"{app}: new app (version {new_version}).")
            continue

        old_version = parse_version(old_content)
        if old_version is None:
            print(f"{app}: version '{new_version}' added — OK.")
            continue

        try:
            verdict = classify(normalize(old_version), normalize(new_version))
        except ValueError as exc:
            failures.append(f"{app}: {exc}")
            continue

        if verdict == "same":
            failures.append(
                f"{app}: content changed but version stayed at '{old_version}' "
                f"— bump it in {path} or nobody is offered the update"
            )
        elif verdict.startswith("ERROR:"):
            failures.append(f"{app}: {old_version} -> {new_version}: {verdict[7:]}")
        else:
            print(f"{app}: {old_version} -> {new_version} ({verdict}) — OK.")

    if failures:
        print("\nVersion check failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\n{len(apps)} app(s) checked — all good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
