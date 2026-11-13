"""/ticker and its suggestions through DIME Terminal's CoinMarketCap relay.

Each case runs the real script with TERMINAL_JEV_URL set and fetch stubbed:
the relay (`/api/cmc/...`) and CoinGecko answer as each case says. Nothing
leaves the machine.
"""

import json
import pathlib
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parents[4] / "apps/market-data/skills/ticker/scripts"

STUB = """
const answers = JSON.parse(process.env.ANSWERS);
globalThis.fetch = async (url) => {
  console.error("URL " + url);
  const path = String(url).split("/api/cmc/")[1]?.split("?")[0];
  const key = path === undefined ? "gecko" : "cmc";
  const answer = answers["cmc:" + path] ?? answers[key] ?? { status: 404 };
  return new Response(JSON.stringify(answer.body ?? {}), { status: answer.status ?? 200 });
};
"""

JEV = "http://127.0.0.1:8091/api/jev"

MAP = {"data": [
    {"symbol": "ETC", "name": "Ethereum Classic", "rank": 40},
    {"symbol": "KAITO", "name": "KAITO", "rank": 300},
    {"symbol": "ETH", "name": "Ethereum", "rank": 2},
    {"symbol": "ETHFI", "name": "ether.fi", "rank": 120},
]}

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def run(script, typed, answers, env_extra=None, tmp=None, stderr=False):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "ANSWERS": json.dumps(answers), "TERMINAL_JEV_URL": JEV}
    if tmp:
        env["TMPDIR"] = tmp
    env.update(env_extra or {})
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + STUB, str(SCRIPTS / script), typed],
        capture_output=True, text=True, timeout=30, env=env,
    )
    if stderr:
        return result.returncode, result.stdout, result.stderr
    urls = [line[4:] for line in result.stderr.splitlines() if line.startswith("URL ")]
    return result.returncode, result.stdout, urls


# /ticker: test_views.py reads the relay's answers; here it refuses.
gecko = {"body": {"btc": {"usd": 74000, "usd_24h_change": 1}}}
for status in (401, 429):
    code, out, urls = run("command.mjs", "btc", {"cmc": {"status": status}, "gecko": gecko})
    check(f"a relay {status} falls back to CoinGecko", code == 0 and out.splitlines() == ["- BTC $74,000 (+1.00% 24h)"] and len(urls) == 2)

# A sidecar from before the relay, or a LiteLLM with no CoinMarketCap route.
code, out, urls = run("command.mjs", "btc", {"cmc": {"status": 404}, "gecko": gecko})
check("a relay 404 falls back to CoinGecko and says more detail isn't available",
      code == 0 and out.splitlines() == ["- BTC $74,000 (+1.00% 24h)", "", "More detail isn't available on this agent."])
UNAVAILABLE = "The market overview isn't available on this agent. Type /ticker btc for a price."
AGAIN = "The market overview could not be read. Try again in a minute."
code, _, err = run("command.mjs", "", {"cmc": {"status": 404}}, stderr=True)
check("the market overview on a relay 404 says it isn't available here", code == 1 and UNAVAILABLE in err)
code, _, err = run("command.mjs", "", {"cmc": {"status": 503}}, stderr=True)
check("the market overview on a relay 503 asks to try again", code == 1 and AGAIN in err)
code, _, err = run("command.mjs", "", {"cmc": {"status": 503}, "cmc:v1/altcoin-season-index/latest": {"status": 404}}, stderr=True)
check("a 404 on a side read leaves a failed overview a retry", code == 1 and AGAIN in err)

code, out, urls = run("command.mjs", "btc", {"gecko": gecko}, {"TERMINAL_JEV_URL": ""})
check("no relay URL asks CoinGecko only, and says more detail isn't available",
      code == 0 and len(urls) == 1 and "coingecko" in urls[0] and out.splitlines()[-1] == "More detail isn't available on this agent.")

# Suggestions
with tempfile.TemporaryDirectory() as tmp:
    code, out, urls = run("lookup.mjs", "btc et", {"cmc": {"body": MAP}}, tmp=tmp)
    rows = json.loads(out)["rows"] if code == 0 else []
    check("suggestions come from the relay's directory, by rank",
          [r["id"] for r in rows] == ["eth", "etc", "ethfi"] and rows[0]["run"] == "/ticker btc eth")
    check("the directory is read once", len(urls) == 1 and urls[0].endswith("/api/cmc/v1/cryptocurrency/map"))

    code, out, urls = run("lookup.mjs", "kai", {}, tmp=tmp)
    check("a coin outside the top 250 is found, from the cached directory with no call",
          urls == [] and [r["id"] for r in json.loads(out)["rows"]] == ["kaito"])

    cache = pathlib.Path(tmp) / "market-data-ticker-lookup" / "cmc-map.json"
    held = json.loads(cache.read_text())
    cache.write_text(json.dumps({"at": held["at"] - 2 * 86_400_000, "coins": held["coins"]}))
    code, out, urls = run("lookup.mjs", "kai", {"cmc": {"status": 503}}, tmp=tmp)
    check("a failed directory refresh answers from the old directory",
          code == 0 and [r["id"] for r in json.loads(out)["rows"]] == ["kaito"] and len(urls) == 1 and "/api/cmc/" in urls[0])

with tempfile.TemporaryDirectory() as tmp:
    top = {"body": [{"symbol": "eth", "name": "Ethereum", "market_cap_rank": 2}]}
    code, out, urls = run("lookup.mjs", "et", {"cmc": {"status": 401}, "gecko": top}, tmp=tmp)
    check("a relay that refuses falls back to CoinGecko's list",
          code == 0 and [r["id"] for r in json.loads(out)["rows"]] == ["eth"] and len(urls) == 2)

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
