#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
collect.py — resolve one Option Monitor block and its week of history.

    uv run scripts/collect.py <VENUE:block_id> --out-dir DIR [--fill-json FILE]

The block comes from the JSON the terminal attached when there is one (the row
the user clicked, and the tape beside it), else from Paradex's public options
tape. History is every block on that tape in the last week with the same
structure, on any venue.

Writes DIR/block.json and DIR/history.json. Exit codes, each with one
`analyze:` line on stderr written for the user:
  0  resolved
  2  the reference is not VENUE:block_id
  4  the tape could not be read and nothing was attached
  5  no such block on the tape's last 7 days
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

TAPE = os.environ.get("PARADEX_TAPE_URL") or "https://api.prod.paradex.trade/v1/options-tape"
# The tape answers only for the last 168h; a minute short keeps a slow clock
# from asking past it.
WINDOW_MS = 7 * 86400_000 - 60_000
PAGE_SIZE = 1000
MAX_PAGES = 10
TIMEOUT_S = 8
# The history read stops starting pages here and says it is partial, so a
# slow tape costs the count, never the block: analyze.sh gives this whole step
# 40s, and one more page can take two 8s attempts.
HISTORY_BUDGET_S = 20
STARTED = time.monotonic()


def say(msg):
    print(f"analyze: {msg}", file=sys.stderr)


def _transient(exc) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def _get(params):
    url = f"{TAPE}?{urlencode(params)}"
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
                return json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 — re-raised unless one retry may fix it
            if attempt == 2 or not _transient(exc):
                raise
            time.sleep(0.3)


def pages(now_ms, underlying=None):
    """Every page of the last week's tape, newest first, up to MAX_PAGES.
    Yields (blocks, more) where more is True when the tape had more pages
    than were read."""
    params = {"page_size": PAGE_SIZE, "start_time": now_ms - WINDOW_MS, "end_time": now_ms}
    if underlying:
        params["underlying"] = underlying
    for n in range(MAX_PAGES):
        page = _get(params)
        cursor = page.get("next")
        yield page.get("results") or [], bool(cursor) and n == MAX_PAGES - 1
        if not cursor:
            return
        params = {"cursor": cursor, "page_size": PAGE_SIZE}


def _blocks_in(value):
    """Every tape block inside an attached JSON value: one block, a list of
    them (nested, since the terminal sends every value it found as one list),
    or a page's {results}."""
    if isinstance(value, list):
        for v in value:
            yield from _blocks_in(v)
    elif isinstance(value, dict):
        if isinstance(value.get("results"), list):
            yield from _blocks_in(value["results"])
        elif "block_id" in value and "venue" in value and isinstance(value.get("legs"), list):
            yield value


def _pick(blocks, venue, block_id):
    """The latest revision of (venue, block_id), unless that is a tombstone."""
    best = None
    for b in blocks:
        if b.get("venue") == venue and str(b.get("block_id")) == block_id:
            if best is None or (b.get("revision") or 0) >= (best.get("revision") or 0):
                best = b
    return None if best is None or best.get("removed") else best


def load_injected(path):
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return []
    if text.startswith("DATA:"):
        text = text[5:].strip()
    try:
        return list(_blocks_in(json.loads(text)))
    except ValueError:
        return []


def find_on_tape(venue, block_id, now_ms):
    seen = []
    for blocks, _ in pages(now_ms):
        seen += [b for b in blocks if b.get("venue") == venue and str(b.get("block_id")) == block_id]
        if seen:
            break
    return _pick(seen, venue, block_id)


def history(block, now_ms):
    """Same-structure blocks on the last week's tape, by venue, without this
    one; {unavailable: true} when the tape could not be read."""
    key = core.structure_key(block)
    if key is None:
        return {"unavailable": True}
    me = (block.get("venue"), str(block.get("block_id")))
    seen, truncated = {}, False
    try:
        for blocks, more in pages(now_ms, block.get("underlying")):
            for b in blocks:
                ident = (b.get("venue"), str(b.get("block_id")))
                if ident == me or b.get("removed") or not b.get("complete", True):
                    continue
                if core.structure_key(b) == key:
                    seen[ident] = b.get("venue")
            truncated = more
            if time.monotonic() - STARTED > HISTORY_BUDGET_S:
                truncated = True
                break
    except Exception as e:  # noqa: BLE001 — the block still stands without its history
        print(f"history: {e}", file=sys.stderr)
        return {"unavailable": True}
    by_venue = {}
    for v in seen.values():
        by_venue[v] = by_venue.get(v, 0) + 1
    return {"count": len(seen), "by_venue": by_venue, "truncated": truncated, "days": 7}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--fill-json")
    ap.add_argument("--now-ms", type=int)
    a = ap.parse_args(argv)
    now_ms = a.now_ms or int(time.time() * 1000)

    ref = core.parse_ref(a.ref)
    if not ref:
        say("expected a block reference like DBT:BLOCK-302780 — the venue, a colon, and the block id.")
        return 2
    venue, block_id = ref

    block = _pick(load_injected(a.fill_json), venue, block_id)
    if block is None:
        try:
            block = find_on_tape(venue, block_id, now_ms)
        except Exception as e:  # noqa: BLE001
            say(f"Paradex's options tape could not be read ({type(e).__name__}), so {venue}:{block_id} "
                f"could not be looked up. Try again in a minute.")
            return 4
        if block is None:
            say(f"Couldn't find {venue}:{block_id} on Paradex's options tape for the last 7 days.")
            return 5

    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "block.json"), "w", encoding="utf-8") as f:
        json.dump(block, f)
    with open(os.path.join(a.out_dir, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history(block, now_ms), f)
    print(f"legs={len(block.get('legs') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
