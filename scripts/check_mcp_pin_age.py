#!/usr/bin/env python3
"""Fail when an app's MCP server pins a third-party package under 14 days old.

The agent image refuses any version that young (uv's exclude-newer, npm's
min-release-age), so such a pin is a server that fails to start on every agent
until the version ages. Reads `mcp_server.command` and `args` from each
apps/<id>/app.yaml and looks the pins up on PyPI and the npm registry.

    python3 scripts/check_mcp_pin_age.py
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

MIN_AGE = dt.timedelta(days=14)
# Ours, published and pinned the same day; the agent image exempts the same three.
FIRST_PARTY_PYPI = {"mcp-paradex", "mcp-paradigm", "paradex-py"}

PYPI_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;]+)$")
NPM_PIN_RE = re.compile(r"^(@?[^@\s]+)@(\d[^\s]*)$")


def pins(command: str, args: list[str]) -> list[tuple[str, str, str]]:
    """(registry, name, version) for every exact pin the server's launch installs."""
    found = []
    if command == "uvx":
        for arg in args:
            m = PYPI_PIN_RE.match(arg.strip())
            if m and m[1].lower() not in FIRST_PARTY_PYPI:
                found.append(("pypi", m[1], m[2]))
    elif command == "npx":
        for arg in args:
            m = NPM_PIN_RE.match(arg)
            if m and not arg.startswith("-"):
                found.append(("npm", m[1], m[2]))
    return found


def published(registry: str, name: str, version: str) -> dt.datetime:
    if registry == "pypi":
        url = f"https://pypi.org/pypi/{name}/{version}/json"
        with urllib.request.urlopen(url, timeout=30) as r:
            stamp = min(f["upload_time_iso_8601"] for f in json.load(r)["urls"])
    else:
        url = f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}"
        with urllib.request.urlopen(url, timeout=30) as r:
            stamp = json.load(r)["time"][version]
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def main() -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - MIN_AGE
    failures, checked = [], 0
    for manifest in sorted(Path("apps").glob("*/app.yaml")):
        server = (yaml.safe_load(manifest.read_text()) or {}).get("mcp_server") or {}
        for registry, name, version in pins(server.get("command", ""), server.get("args") or []):
            checked += 1
            try:
                at = published(registry, name, version)
            except Exception as exc:  # an unreadable pin is not a passing one
                failures.append(f"{manifest}: {name}=={version} publish time unreadable ({exc})")
                continue
            if at > cutoff:
                failures.append(f"{manifest}: {name} {version} was published {at:%Y-%m-%d}, "
                                f"under {MIN_AGE.days} days old, so the agent cannot install it")
    print(f"{checked} MCP server pin(s) checked")
    for line in failures:
        print(f"  {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
