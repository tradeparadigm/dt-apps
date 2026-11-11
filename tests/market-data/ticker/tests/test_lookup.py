"""/ticker's suggestion script, with fetch stubbed in node.

Each case runs the real script with what a user typed after `/ticker `, with its
cache in a fresh directory, and records the request it makes. Nothing reaches
CoinGecko.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[4]
    / "apps/market-data/skills/ticker/scripts/lookup.mjs"
)

STUB = """
const answer = JSON.parse(process.env.LOOKUP_ANSWER);
globalThis.fetch = async (url) => {
  console.error("URL " + url);
  if (answer.throw) throw new TypeError("fetch failed", { cause: { code: answer.throw } });
  return new Response(JSON.stringify(answer.body ?? {}), { status: answer.status ?? 200 });
};
"""

# CoinGecko's /coins/markets answer, trimmed: ordered by market cap.
MARKETS = [
    {"symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1},
    {"symbol": "eth", "name": "Ethereum", "market_cap_rank": 2},
    {"symbol": "usdt", "name": "Tether", "market_cap_rank": 3},
    {"symbol": "xaut", "name": "Ethena Gold", "market_cap_rank": 30},
    {"symbol": "etc", "name": "Ethereum Classic", "market_cap_rank": 40},
    {"symbol": "eth", "name": "Bridged Ether", "market_cap_rank": 90},
    {"symbol": "et.h", "name": "Etched", "market_cap_rank": 200},
]

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def lookup(typed, cache, **answer):
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + STUB, str(SCRIPT), typed],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TMPDIR": cache, "LOOKUP_ANSWER": json.dumps(answer)},
    )
    urls = [line[4:] for line in result.stderr.splitlines() if line.startswith("URL ")]
    errors = [line for line in result.stderr.splitlines() if not line.startswith("URL ")]
    rows = json.loads(result.stdout)["rows"] if result.returncode == 0 else None
    return result.returncode, rows, errors, urls


with tempfile.TemporaryDirectory() as cache:
    code, rows, _, urls = lookup("", cache)
    check("nothing typed lists the majors without a call", code == 0 and urls == [] and rows[0]["run"] == "/ticker btc" and len(rows) == 8)

    code, rows, _, urls = lookup("btc ", cache)
    check("after a symbol and a space, the majors add to it", urls == [] and rows[0]["run"] == "/ticker btc eth" and all(r["id"] != "btc" for r in rows))

    code, rows, _, urls = lookup("btc et", cache, body=MARKETS)
    check("one call reads the top coins by market cap", len(urls) == 1 and "/coins/markets?" in urls[0] and "per_page=250" in urls[0])
    check("a symbol that starts with it comes first, then a name, each by market cap",
          [r["id"] for r in rows] == ["eth", "etc", "xaut"])
    check("a row runs /ticker with the symbols already typed",
          rows[0] == {"id": "eth", "lead": {"text": "ETH", "tone": "accent"}, "main": "Ethereum",
                      "trail": {"text": "#2", "tone": "muted"}, "run": "/ticker btc eth"})

    code, rows, _, urls = lookup("$ET", cache, body=[])
    check("within five minutes the list answers from the cache", urls == [] and [r["id"] for r in rows][:2] == ["eth", "etc"])

    code, rows, _, urls = lookup("u", cache, body=[])
    check("another word typed makes no call either", urls == [] and [r["id"] for r in rows] == ["usdt"])

    code, rows, _, urls = lookup("btc,et", cache, body=[])
    check("a comma separates symbols as /ticker reads them", urls == [] and rows[0]["run"] == "/ticker btc eth")

    entry = pathlib.Path(cache, "market-data-ticker-lookup", "markets.json")
    stale = json.loads(entry.read_text())
    stale["at"] = int(time.time() * 1000) - 6 * 60_000
    entry.write_text(json.dumps(stale))
    code, rows, _, urls = lookup("et", cache, body=[{"symbol": "eth", "name": "Ethereum", "market_cap_rank": 2}])
    check("a cached answer older than five minutes is asked again", len(urls) == 1 and [r["id"] for r in rows] == ["eth"])

    code, rows, _, urls = lookup("eth et", cache)
    check("a symbol already typed is not offered again", urls == [] and rows == [])

    code, rows, _, urls = lookup("eth/usd", cache)
    check("a word /ticker would refuse searches nothing", code == 0 and rows == [] and urls == [])


with tempfile.TemporaryDirectory() as cache:
    code, _, errors, _ = lookup("so", cache, status=429)
    check("a refusal is one line and no rows", code == 1 and errors == ["CoinGecko answered 429."])
    check("a refusal is not cached", not pathlib.Path(cache, "market-data-ticker-lookup", "markets.json").exists())

    code, _, errors, _ = lookup("so", cache, throw="ENOTFOUND")
    check("a network failure is one line", code == 1 and errors == ["Could not reach CoinGecko: ENOTFOUND."])

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
