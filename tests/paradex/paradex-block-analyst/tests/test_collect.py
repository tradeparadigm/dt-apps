#!/usr/bin/env python3
"""collect.py with the options tape stubbed. Run: python3 tests/test_collect.py"""
import contextlib
import io
import json
import os
import tempfile

from _harness import TAPE, Tally, block
import collect

t = Tally()
NOW = 1791951051702
DBT = "DBT:BLOCK-302780"


def run(ref, injected=None, pages=None, fail=False):
    """(exit code, stderr, block.json, history.json, tape requests)."""
    asked = []

    def fake_get(params):
        asked.append(dict(params))
        if fail:
            raise OSError("down")
        i = int(params.get("cursor") or 0)
        book = pages or [TAPE]
        return {"results": book[i], "next": str(i + 1) if i + 1 < len(book) else None}

    saved, collect._get = collect._get, fake_get
    with tempfile.TemporaryDirectory() as d:
        args = [ref, "--out-dir", d, "--now-ms", str(NOW)]
        if injected is not None:
            path = os.path.join(d, "in.json")
            with open(path, "w") as f:
                f.write(injected if isinstance(injected, str) else json.dumps(injected))
            args += ["--fill-json", path]
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = collect.main(args)
        finally:
            collect._get = saved

        def read(name):
            p = os.path.join(d, name)
            return json.load(open(p)) if os.path.exists(p) else None
        return code, err.getvalue(), read("block.json"), read("history.json"), asked


# the attached block wins, wherever it sits in what the terminal sent
code, _, b, _, asked = run(DBT, injected=[[{"note": 1}, block(DBT)], {"results": []}])
t.ok(code == 0 and b["block_id"] == "BLOCK-302780", "block found inside nested attached JSON")
t.ok(all("underlying" in a or "cursor" in a for a in asked), "no tape lookup for the block itself")
code, _, b, _, _ = run(DBT, injected="DATA: " + json.dumps(block(DBT)), pages=[[]])
t.ok(code == 0 and b, "a DATA: label is stripped")

newer = dict(block(DBT), revision=2, quantity="11")
code, _, b, _, _ = run(DBT, injected=[block(DBT), newer])
t.ok(b["quantity"] == "11", "the highest revision wins")
gone = dict(block(DBT), revision=3, removed=True)
code, err, b, _, _ = run(DBT, injected=[block(DBT), gone], pages=[[]])
t.ok(code == 5 and b is None, "a tombstone is not analysed")

# off the tape when nothing is attached
code, _, b, h, asked = run("PRDX:1791942177030201709226870000", pages=[[block(DBT)], TAPE, [block(DBT)]])
lookups = next(i for i, a in enumerate(asked) if "underlying" in a)
t.ok(code == 0 and lookups == 2, "the lookup stops at the page that has it")
code, _, b, h, asked = run("PRDX:1791942177030201709226870000")
t.ok(code == 0 and b["venue"] == "PRDX", "found on the tape")
t.ok(asked[0]["end_time"] == NOW and NOW - asked[0]["start_time"] < 7 * 86400_000,
     "asks only inside the tape's 168h")
t.ok(any(a.get("underlying") == "ETH" for a in asked), "history reads the block's coin only")

code, err, *_ = run("DBT:NOPE")
t.ok(code == 5 and "Couldn't find DBT:NOPE" in err, "not on the tape: exit 5 with one line")
code, err, *_ = run(DBT, fail=True)
t.ok(code == 4 and err.startswith("analyze: Paradex's options tape could not be read"),
     "tape down, nothing attached: exit 4")
code, err, *_ = run("BLOCK-302780")
t.ok(code == 2 and err.startswith("analyze: expected a block reference"), "bad reference: exit 2")

# history: same structure on any venue, never itself, never a tombstone
twin = dict(block(DBT), venue="DRV", block_id="x1")
dead = dict(block(DBT), venue="OKX", block_id="x2", removed=True)
other = dict(block(DBT), block_id="x3",
             legs=[dict(block(DBT)["legs"][0], strike="1")] + block(DBT)["legs"][1:])
partial = dict(block(DBT), venue="BLSH", block_id="x4", complete=False)
code, _, _, h, _ = run(DBT, injected=[block(DBT)], pages=[[block(DBT), twin, dead, partial], [other]])
t.ok(h == {"count": 1, "by_venue": {"DRV": 1}, "truncated": False, "days": 7},
     f"history counts the twin only: {h}")

full = [dict(block(DBT), block_id=f"f{i}") for i in range(collect.PAGE_SIZE)]
code, _, _, h, _ = run(DBT, injected=[block(DBT)], pages=[full] * collect.MAX_PAGES + [[]])
t.ok(h["truncated"] and h["count"] == collect.PAGE_SIZE, "a capped read says so")

code, _, _, h, _ = run(DBT, injected=[block(DBT)], pages=[full, []])
t.ok(not h["truncated"], "a full last page with no cursor read everything")

saved_started = collect.STARTED
collect.STARTED -= collect.HISTORY_BUDGET_S + 1
code, _, _, h, asked = run(DBT, injected=[block(DBT)], pages=[full, full, full])
collect.STARTED = saved_started
t.ok(h["truncated"] and len(asked) == 1, "past its budget the history stops after the page in hand")

code, _, b, h, _ = run(DBT, injected=[block(DBT)], fail=True)
t.ok(code == 0 and b and h == {"unavailable": True}, "history down still analyses the block")

t.done()
