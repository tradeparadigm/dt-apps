#!/usr/bin/env python3
"""
Offline checks on the JEV evals (evals/jev_cases.json) — no network.
Run: python3 tests/test_jev_cases.py

The evals themselves need JEV and are run by hand (evals/jev_evals.py). These
keep the cases honest between runs: every case still builds a request through
interest.build_request, every question the script asks has a case, every
expected answer is one the question offers, and a spread's price direction is
worked out rather than asked.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("jev_evals", HERE.parent / "evals" / "jev_evals.py")
jev_evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev_evals)
interest = jev_evals.interest

_p = _f = 0


def ok(cond, msg):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  {msg}")
    else:
        _f += 1
        print(f"FAIL  {msg}")


cases, today = jev_evals.load()
ids = [c["id"] for c in cases]
ok(len(ids) == len(set(ids)), "case ids are unique")

all_questions = {**interest.BET_QUESTIONS, **interest.HISTORY_QUESTIONS}
covered = set()
for case in cases:
    body, coin, price_bet = jev_evals.request_for(case, today)
    asked = body["questions"]
    for q, expected in case["expect"].items():
        covered.add(q)
        spec_ = all_questions.get(q)
        if spec_ is None:
            ok(False, f"{case['id']}: {q} is not a question interest.py asks")
            continue
        ok(q in asked, f"{case['id']}: {q} is asked for this case")
        if spec_["type"] == "score":
            ok(isinstance(expected, int) and 0 <= expected < len(spec_["criteria"]),
               f"{case['id']}: {q} expects a level the score has ({expected})")
        else:
            ok(expected in spec_["criteria"], f"{case['id']}: {q} expects an option it offers ({expected})")
    if case.get("price_worked_out"):
        ok(price_bet in ("up", "down") and "trade_price_bet" not in asked,
           f"{case['id']}: the price direction is worked out ({price_bet}), not asked")
    ok(case["trades"] == [] or "trade_history" in body["state"],
       f"{case['id']}: its trades reach JEV as history (inside the 90 days)")

ok(covered == set(all_questions), f"every question has a case [{sorted(set(all_questions) - covered)} uncovered]")

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
