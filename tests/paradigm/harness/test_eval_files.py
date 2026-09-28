#!/usr/bin/env python3
"""Every evals.json, checked for what run_evals.py reads out of it.

These files sit beside the tests rather than under the skill, so nothing in
the app checker looks at them. A missing key here passes every CI job and
fails when someone runs the evals.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALS = sorted((HERE.parent).glob("*/evals/evals.json"))
_p = _f = 0


def ok(cond, msg):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  {msg}")
    else:
        _f += 1
        print(f"  ✗   {msg}")


ok(len(EVALS) >= 4, f"found {len(EVALS)} eval files")

for f in EVALS:
    skill = f.parent.parent.name
    try:
        d = json.loads(f.read_text())
    except json.JSONDecodeError as e:
        ok(False, f"{skill}: evals.json parses ({e})")
        continue
    ok(isinstance(d, dict), f"{skill}: evals.json is an object")
    # run_evals.py reads this one unguarded when it reports a result.
    ok(d.get("skill_name") == skill,
       f"{skill}: skill_name is {d.get('skill_name')!r}, directory is {skill!r}")
    ok(any(k in d for k in ("evals", "cases", "trigger_evals")),
       f"{skill}: carries at least one set of cases")

# The RFQ skill moved off an MCP server onto REST. An eval that still grades
# the old tool names, the old response keys, or a broadcast the service
# refuses marks the correct behaviour as a failure.
STALE = ("paradigm_drfqv2", "paradigm_echo", "has_more", "prime-venue-enabled",
         "empty/omitted broadcast")
for f in EVALS:
    body = f.read_text()
    found = [s for s in STALE if s in body]
    ok(not found, f"{f.parent.parent.name}: no stale expectations {found or ''}")

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
