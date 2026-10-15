"""Shared by this skill's tests: the scripts on sys.path, the tape fixture,
and a tally that prints "N passed" for CI."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import skillpath  # noqa: E402

sys.path.insert(0, str(skillpath.scripts("paradex-block-analyst")))

TAPE = json.loads((HERE / "fixtures" / "tape-2026-10-14.json").read_text())


def block(ref):
    venue, block_id = ref.split(":", 1)
    return next(b for b in TAPE if b["venue"] == venue and b["block_id"] == block_id)


class Tally:
    def __init__(self):
        self.passed = self.failed = 0

    def ok(self, cond, msg):
        if cond:
            self.passed += 1
        else:
            self.failed += 1
            print(f"  FAIL: {msg}")

    def done(self):
        print(f"\n{self.passed} passed, {self.failed} failed")
        sys.exit(1 if self.failed else 0)
