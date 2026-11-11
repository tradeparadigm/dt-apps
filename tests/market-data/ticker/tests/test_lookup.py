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

# CoinGecko's /search answer for "et", trimmed: ordered by market cap.
ET = {"coins": [
    {"symbol": "USDT", "name": "Tether", "market_cap_rank": 3},
    {"symbol": "ETH", "name": "Ethereum", "market_cap_rank": 2},
    {"symbol": "XAUT", "name": "Ethena Gold", "market_cap_rank": 30},
    {"symbol": "ETC", "name": "Ethereum Classic", "market_cap_rank": 40},
    {"symbol": "ETH", "name": "Bridged Ether", "market_cap_rank": None},
    {"symbol": "ET.H", "name": "Dotted", "market_cap_rank": 900},
]}

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

    code, rows, _, urls = lookup("btc et", cache, body=ET)
    check("the last word is searched", len(urls) == 1 and urls[0].endswith("/search?query=et"))
    check("a symbol that starts with it comes first, then a name, then the rest",
          [r["id"] for r in rows] == ["eth", "etc", "xaut", "usdt"])
    check("a row runs /ticker with the symbols already typed",
          rows[0] == {"id": "eth", "lead": {"text": "ETH", "tone": "accent"}, "main": "Ethereum",
                      "trail": {"text": "#2", "tone": "muted"}, "run": "/ticker btc eth"})

    code, rows, _, urls = lookup("$ET", cache, body={"coins": []})
    check("the same search within five minutes answers from the cache", urls == [] and [r["id"] for r in rows][:2] == ["eth", "etc"])

    entry = pathlib.Path(cache, "market-data-ticker-lookup", "et.json")
    stale = json.loads(entry.read_text())
    stale["at"] = int(time.time() * 1000) - 6 * 60_000
    entry.write_text(json.dumps(stale))
    code, rows, _, urls = lookup("et", cache, body={"coins": [{"symbol": "ETH", "name": "Ethereum", "market_cap_rank": 2}]})
    check("a cached answer older than five minutes is asked again", len(urls) == 1 and [r["id"] for r in rows] == ["eth"])

    code, rows, _, urls = lookup("eth et", cache)
    check("a symbol already typed is not offered again", urls == [] and rows == [])

    code, rows, _, urls = lookup("eth/usd", cache)
    check("a word /ticker would refuse searches nothing", code == 0 and rows == [] and urls == [])

    code, _, errors, _ = lookup("so", cache, status=429)
    check("a refusal is one line and no rows", code == 1 and errors == ["CoinGecko answered 429."])
    check("a refusal is not cached", not pathlib.Path(cache, "market-data-ticker-lookup", "so.json").exists())

    code, _, errors, _ = lookup("so", cache, throw="ENOTFOUND")
    check("a network failure is one line", code == 1 and errors == ["Could not reach CoinGecko: ENOTFOUND."])

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
