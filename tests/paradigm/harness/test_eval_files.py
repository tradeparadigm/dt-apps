#!/usr/bin/env python3
"""Every evals.json, checked for what run_evals.py reads out of it.

These files sit beside the tests rather than under a skill, so nothing in the
app checker looks at them. A missing key here passes every CI job and fails
when someone runs the evals, which is the one time nobody is watching.

Keys are read from run_evals.py rather than listed here, so a new unguarded
read is caught by this file rather than by a stack trace.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVALS = sorted(ROOT.glob("*/evals/evals.json"))
RUNNER = (ROOT / "run_evals.py").read_text()
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

# What run_evals.py subscripts on an eval file without a default. The top-level
# set is read out of the source, so a new unguarded read shows up here. The
# per-case set is pinned, because the same `case[...]` spelling is also used on
# RESULT rows the runner builds itself, and those are not our business. Each
# pinned key is checked against the source, so dropping a read fails this too.
top = set(re.findall(r'evals_data\["([a-z_]+)"\]', RUNNER))
case = {"id", "prompt", "assertions"}
ok(bool(top), f"found the top-level keys run_evals reads: {sorted(top)}")
for key in sorted(case):
    ok(f'case["{key}"]' in RUNNER or f"case.get(\"{key}\"" in RUNNER or f"c.get(\"{key}\"" in RUNNER
       or f"case['{key}']" in RUNNER,
       f"run_evals still reads case[{key!r}]")

for f in EVALS:
    skill = f.parent.parent.name
    try:
        d = json.loads(f.read_text())
    except json.JSONDecodeError as e:
        ok(False, f"{skill}: evals.json parses ({e})")
        continue
    ok(isinstance(d, dict), f"{skill}: evals.json is an object")
    ok(d.get("skill_name") == skill,
       f"{skill}: skill_name is {d.get('skill_name')!r}, directory is {skill!r}")
    for key in sorted(top):
        ok(key in d, f"{skill}: carries {key!r}, which run_evals reads unguarded")
    cases = d.get("evals") or []
    ok(len(cases) > 0, f"{skill}: has {len(cases)} eval cases")
    for c in cases:
        for key in sorted(case):
            if key not in c:
                ok(False, f"{skill}: case {c.get('id')} has no {key!r}")
                break
        else:
            continue
        break
    else:
        ok(True, f"{skill}: every case carries {sorted(case)}")

# The RFQ skill moved off an MCP server onto REST. An eval still grading the
# old world marks the correct behaviour as a failure. Matched as patterns, not
# as fixed strings, so a respelling does not slip through.
STALE = [
    # block_analyst and options_recap are slash commands, not tool names:
    # OpenClaw turns a skill's hyphens into underscores to name its command.
    (r"paradigm_(?!trade|data|executions|rfq_tape|block_analyst|options_recap)[a-z_]+",
     "an MCP tool name"),
    # The same names shorn of their prefix. A rename pass that rewrote
    # paradigm_drfqv2_create_rfq to create_rfq left one of these behind, and
    # the prefixed pattern above cannot see it.
    (r"\b(create_rfq|post_order|price_legs|rfq_snapshot|kill_switch|desk_overview)\b",
     "a tool name with its prefix stripped"),
    (r"\bhas_more\b", "a response key the endpoint does not return"),
    (r"prime[- ]venue[- ]enabled", "a counterparty flag that does not exist"),
    (r"empty\s*/?\s*(omitted\s*)?broadcast", "a broadcast the service refuses"),
    (r"mcp[- ]paradigm|\.mcpb|MCP server config", "MCP setup"),
]
# One assertion names MCP on purpose, to require that the answer does NOT
# describe it. Cut exactly that string before scanning, rather than letting its
# presence excuse the whole file: a file-wide allowance means an install guide
# pasted anywhere else in it passes.
INTENTIONAL = (
    "Response does NOT describe an MCP server, a .mcpb bundle, or PARADIGM_* "
    "environment variables"
)

_cut = 0
for f in EVALS:
    body = f.read_text()
    scanned = body.replace(INTENTIONAL, "")
    if INTENTIONAL in body:
        _cut += 1
    for pat, what in STALE:
        hits = re.findall(pat, scanned, re.I)
        ok(not hits, f"{f.parent.parent.name}: no {what} {sorted(set(hits))[:3] if hits else ''}")

# The cut above is only safe while that exact string is still what an eval
# file carries. Reword the assertion and the cut stops matching, so every MCP
# mention fails instead of the intended one passing.
ok(_cut > 0, f"the intentional MCP assertion still matches an eval file ({_cut})")

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
