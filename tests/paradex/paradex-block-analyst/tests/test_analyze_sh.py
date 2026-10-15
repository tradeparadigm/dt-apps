#!/usr/bin/env python3
"""analyze.sh's routing, with `uv` stubbed so each step answers as told.
Run: python3 tests/test_analyze_sh.py"""
import os
import subprocess
import tempfile
from pathlib import Path

from _harness import Tally
import skillpath

t = Tally()
SCRIPT = skillpath.scripts("paradex-block-analyst") / "analyze.sh"

FAKE_UV = r'''#!/usr/bin/env bash
# uv run <script> args… → act as $FAKE_<STEP> says: "<exit>|<stdout>|<stderr>"
shift
case "$1" in
  *collect.py) spec="$FAKE_COLLECT"; out=""; fill=""
    for a in "$@"; do [ "$prev" = "--out-dir" ] && out="$a"; [ "$prev" = "--fill-json" ] && fill="$a"; prev="$a"; done
    [ -n "$fill" ] && [ -n "$FAKE_SEEN" ] && cp "$fill" "$FAKE_SEEN"
    [ -n "$out" ] && echo '{"venue":"DBT","block_id":"B1"}' > "$out/block.json" ;;
  *analyze.py) spec="$FAKE_RENDER" ;;
  *interest.py) spec="$FAKE_INTEREST" ;;
esac
IFS='|' read -r code out err <<< "$spec"
[ -n "$out" ] && printf '%b\n' "$out"
[ -n "$err" ] && printf '%b\n' "$err" >&2
exit "${code:-0}"
'''


def run(ref="DBT:B1", collect="0", render="0|BLOCK", interest="0", stdin=None, seen=""):
    with tempfile.TemporaryDirectory() as d:
        uv = Path(d) / "uv"
        uv.write_text(FAKE_UV)
        uv.chmod(0o755)
        env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", FAKE_COLLECT=collect,
                   FAKE_RENDER=render, FAKE_INTEREST=interest, FAKE_SEEN=seen, ANALYZE_INTEREST_GRACE="2")
        args = ["bash", str(SCRIPT), ref] + (["--fill-json", "-"] if stdin is not None else [])
        p = subprocess.run(args, input=stdin or "", capture_output=True, text=True, env=env, timeout=60)
        return p.returncode, p.stdout


for bad in ("-h", "--fill-json", "dbt:1", "DBT:a b"):
    code, out = run(ref=bad, collect="9||must not run")
    t.ok(code == 0 and out.startswith("analyze: expected a block reference"), f"refuses {bad!r} itself")

code, out = run(ref="")
t.ok(code == 0 and out.startswith("analyze: usage: /paradex_analyze"), "no reference: usage, exit 0")

for c in ("2", "4", "5"):
    code, out = run(collect=f"{c}||analyze: the note {c}\nuv chatter")
    t.ok(code == 0 and out == f"analyze: the note {c}\n", f"collect {c}: its own line only, exit 0")

code, out = run(collect="3||Traceback boom")
t.ok(code == 1 and "## For the agent" in out and "Traceback boom" in out, "collect crash hands off")
with tempfile.TemporaryDirectory() as d:
    seen = os.path.join(d, "seen.json")
    run(stdin='[{"venue":"DBT"}]', seen=seen)
    t.ok(open(seen).read() == '[{"venue":"DBT"}]', "stdin reaches collect as its --fill-json file")

code, out = run(collect="3||boom", stdin="[]")
t.ok("from the block attached" in out, "with JSON attached, the hand-off says to use it")

code, out = run(interest="0|*The taker is betting BTC rises.*")
t.ok(code == 0 and out == "BLOCK\n\n*The taker is betting BTC rises.*\n", f"line after the block: {out!r}")
code, out = run(render="1|BLOCK\\n\\n## For the agent\\n\\ntask", interest="0|LINE")
t.ok(code == 1 and out.index("LINE") < out.index("## For the agent"), "line above the agent's section")
code2, out2 = run(interest="1|", render="0|BLOCK")
t.ok(out2 == "BLOCK\n", "interest failing costs only its line")

code, out = run(render="7|half|Traceback x")
t.ok(code == 1 and '"block_id":"B1"' in out and "## For the agent" in out, "render crash hands the block on")
code, out = run(render="1|no section")
t.ok(code == 1 and "analyze.py failed (exit 1)" in out, "exit 1 without a section is a crash")

t.done()
