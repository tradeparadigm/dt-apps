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
  const key = String(url).includes("/api/cmc/") ? "cmc" : "gecko";
  const answer = answers[key] ?? { status: 404 };
  return new Response(JSON.stringify(answer.body ?? {}), { status: answer.status ?? 200 });
};
"""

JEV = "http://127.0.0.1:8091/api/jev"

QUOTES = {"data": {
    "BTC": [{"cmc_rank": 1, "quote": {"USD": {"price": 75001, "percent_change_24h": -1.05}}}],
    "PEPE": [
        {"cmc_rank": 4054, "quote": {"USD": {"price": 0.5, "percent_change_24h": 9}}},
        {"cmc_rank": 40, "quote": {"USD": {"price": 0.0000123, "percent_change_24h": 2}}},
    ],
}}

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


def run(script, typed, answers, env_extra=None, tmp=None):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "ANSWERS": json.dumps(answers), "TERMINAL_JEV_URL": JEV}
    if tmp:
        env["TMPDIR"] = tmp
    env.update(env_extra or {})
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + STUB, str(SCRIPTS / script), typed],
        capture_output=True, text=True, timeout=30, env=env,
    )
    urls = [line[4:] for line in result.stderr.splitlines() if line.startswith("URL ")]
    return result.returncode, result.stdout, urls


# /ticker
code, out, urls = run("command.mjs", "btc pepe", {"cmc": {"body": QUOTES}})
check("prices come from the relay, the highest-ranked coin of a symbol",
      code == 0 and out.splitlines() == ["- BTC $75,001 (-1.05% 24h)", "- PEPE $0.0000123 (+2.00% 24h)"])
check("one relay call for every symbol, upper case",
      len(urls) == 1 and urls[0].startswith("http://127.0.0.1:8091/api/cmc/v2/cryptocurrency/quotes/latest?symbol=BTC%2CPEPE"))

gecko = {"body": {"btc": {"usd": 74000, "usd_24h_change": 1}}}
for status in (401, 404, 429):
    code, out, urls = run("command.mjs", "btc", {"cmc": {"status": status}, "gecko": gecko})
    check(f"a relay {status} falls back to CoinGecko", code == 0 and out.splitlines() == ["- BTC $74,000 (+1.00% 24h)"] and len(urls) == 2)

code, out, urls = run("command.mjs", "btc", {"gecko": gecko}, {"TERMINAL_JEV_URL": ""})
check("no relay URL asks CoinGecko only", code == 0 and len(urls) == 1 and "coingecko" in urls[0])

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

with tempfile.TemporaryDirectory() as tmp:
    top = {"body": [{"symbol": "eth", "name": "Ethereum", "market_cap_rank": 2}]}
    code, out, urls = run("lookup.mjs", "et", {"cmc": {"status": 401}, "gecko": top}, tmp=tmp)
    check("a relay that refuses falls back to CoinGecko's list",
          code == 0 and [r["id"] for r in json.loads(out)["rows"]] == ["eth"] and len(urls) == 2)

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
