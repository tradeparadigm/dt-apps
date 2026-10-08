#!/usr/bin/env python3
"""
Evals for the questions interest.py asks JEV: run them after any change to a
question's wording, to the trade or history text, or to the JEV model.

Each case in jev_cases.json is a block (and, for the history questions, the
user's trades). Its request is built by interest.build_request, the function
the script itself uses, so this measures exactly what the script would send.
A case passes when JEV gives the expected answer sure enough for the line to
show it. JEV is not deterministic: a case near the threshold can flip between
runs, so --trials N asks each case N times and reports the worst.

    # Anywhere, straight to TypeSafe (what CI would use)
    TYPESAFE_API_KEY=... python3 tests/paradigm/paradigm-block-analyst/evals/jev_evals.py
    # On an agent: the sidecar's relay, no key
    python3 tests/paradigm/paradigm-block-analyst/evals/jev_evals.py
    # Through LiteLLM's TypeSafe route, with a LiteLLM key
    JEV_URL=https://<litellm>/typesafe/v1/systemone JEV_KEY=sk-... python3 .../jev_evals.py

LiteLLM's route only forwards the body to TypeSafe's /v1/systemone with its
own key as a Bearer token, so all three ask the same model the same thing.

    --only kind-       cases whose id starts with this
    --trials 3         ask each case 3 times
    --print kind-far   print one case's request (paste into the playground) and stop

Exits 1 when any case fails. Not run in CI: it needs JEV and spends credit.
tests/test_jev_cases.py checks this file's cases offline, and runs in CI.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import skillpath  # noqa: E402
sys.path.insert(0, str(skillpath.scripts("paradigm-block-analyst")))
import interest  # noqa: E402

CASES = Path(__file__).with_name("jev_cases.json")


def load():
    doc = json.loads(CASES.read_text())
    today = dt.datetime.fromisoformat(doc["today"]).replace(tzinfo=dt.timezone.utc)
    return doc["cases"], today


def request_for(case, today):
    """(request body, coin, worked-out price direction) for one case."""
    block = case["block"]
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
        w = csv.writer(f)
        w.writerow(["PRODUCT", "DESCRIPTION", "QTY", "SIDE", "RFQ_ID", "INSTRUMENT"])
        for instrument, side, qty in block["legs"]:
            w.writerow([f"{instrument.split('-')[0]} OPTION - DBT", block["description"], qty, side,
                        "r_EVAL", instrument])
    try:
        trade, rfq, coin, price_bet = interest.block_trade(f.name)
    finally:
        os.unlink(f.name)
    questions, state = interest.build_request(trade, rfq, price_bet, case["trades"], today)
    return {"model": interest.JEV_MODEL, "questions": questions, "state": state}, coin, price_bet


def verdict(question, expected, ans):
    """(passed, what JEV said) for one question's answer."""
    ans = ans or {}
    if isinstance(expected, int):
        score, conf = interest._num(ans.get("score")), interest._num(ans.get("confidence"))
        if score is None or conf is None:
            return False, "no score"
        ok = round(score) == expected and conf >= interest.MIN_SCORE_CONFIDENCE
        return ok, f"{score:.2f} conf {conf:.0%}"
    choice = ans.get("choice")
    p = interest._num((ans.get("probabilities") or {}).get(choice)) or 0
    ok = choice == expected and p >= interest.MIN_CHOICE_PROBABILITY
    return ok, f"{choice} {p:.0%}"


def ask(url, key, body):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--print", dest="print_id")
    a = ap.parse_args(argv)
    cases, today = load()

    if a.print_id:
        case = next((c for c in cases if c["id"] == a.print_id), None)
        if not case:
            sys.exit(f"no case {a.print_id}")
        body = request_for(case, today)[0]
        print("QUESTIONS\n" + json.dumps(body["questions"]) + "\n\nSTATE\n" + json.dumps(body["state"]))
        return 0

    url = os.environ.get("JEV_URL") or os.environ.get("TERMINAL_JEV_URL") or interest.JEV_URL
    key = os.environ.get("JEV_KEY")
    if os.environ.get("TYPESAFE_API_KEY") and not os.environ.get("JEV_URL"):
        url = (os.environ.get("TYPESAFE_API_BASE") or "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
        key = os.environ["TYPESAFE_API_KEY"]
    print(f"JEV at {url}, {a.trials} trial(s) per case\n")
    failed = 0
    for case in cases:
        if not case["id"].startswith(a.only):
            continue
        body, coin, price_bet = request_for(case, today)
        results = {q: [] for q in case["expect"]}
        for _ in range(a.trials):
            try:
                resp = ask(url, key, body)
            except (urllib.error.URLError, OSError, ValueError) as e:
                for q in results:
                    results[q].append((False, f"error: {e}"))
                continue
            for q, expected in case["expect"].items():
                results[q].append(verdict(q, expected, interest.answer(resp, q)))
        cells = []
        for q, runs in results.items():
            ok = all(r[0] for r in runs)
            failed += not ok
            cells.append(f"{'PASS' if ok else 'FAIL'} {q}={case['expect'][q]} got " + ", ".join(r[1] for r in runs))
        if price_bet:
            cells.append(f"price worked out: {price_bet}")
        print(f"{case['id']:24} " + " | ".join(cells))
    print(f"\n{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
