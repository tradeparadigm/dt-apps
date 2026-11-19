"""/ticker's command script, with fetch stubbed in node.

Each case runs the real script with the arguments a user typed, and records the
request it makes. Nothing reaches CoinGecko. No relay is set, so every case
is an agent without DIME Terminal's relay.
"""

import json
import pathlib
import subprocess
import sys

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[4]
    / "apps/market-data/skills/ticker/scripts/command.mjs"
)

STUB = """
const answer = JSON.parse(process.env.TICKER_ANSWER);
globalThis.fetch = async (url) => {
  console.error("URL " + url);
  if (answer.throw) throw new TypeError("fetch failed", { cause: { code: answer.throw } });
  const body = answer.html ? "<html>Too many requests</html>" : JSON.stringify(answer.body ?? {});
  return new Response(body, { status: answer.status ?? 200 });
};
"""

# Printed after the prices, apart from the list.
NOTE = ["", "More detail isn't available on this agent."]

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def ticker(args, **answer):
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + STUB, str(SCRIPT), args],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TICKER_ANSWER": json.dumps(answer)},
    )
    urls = [line[4:] for line in result.stderr.splitlines() if line.startswith("URL ")]
    errors = [line for line in result.stderr.splitlines() if not line.startswith("URL ")]
    return result.returncode, result.stdout.splitlines(), errors, urls


code, out, _, urls = ticker("BTC, btc $eth", body={"btc": {"usd": 75001, "usd_24h_change": -1.05}, "eth": {"usd": 2144.62, "usd_24h_change": 4.1}})
check("symbols are lowercased, deduped and lose a leading $", len(urls) == 1 and "symbols=btc%2Ceth&" in urls[0])
check("each symbol prints its price and change", code == 0 and out == ["- BTC $75,001 (-1.05% 24h)", "- ETH $2,144.62 (+4.10% 24h)", NOTE[0], NOTE[1]])

for pair in ["btc-usd", "XAU-USD-PERP", "eth/usdt"]:
    code, _, errors, urls = ticker(pair)
    check(f"a market name like {pair} is refused, not priced as a coin", code == 1 and urls == [] and "like btc" in errors[0])

code, out, _, _ = ticker("steth pol", body={"stETH": {"usd": 2107.09}, "POL": {"usd": 0.087607}})
check("a quote CoinGecko keys in mixed case is found", code == 0 and out == ["- STETH $2,107.09", "- POL $0.08761", *NOTE])

code, out, _, _ = ticker("nosuchcoin", body={})
check("a symbol with no quote says so", code == 0 and out == ["- NOSUCHCOIN: no price found", *NOTE])

code, out, errors, urls = ticker("")
check("no symbols says the overview isn't available and calls nothing", code == 1 and urls == [] and errors == ["The market overview isn't available on this agent. Type /ticker btc for a price."])

code, _, errors, urls = ticker("btc ../etc")
check("a symbol that is not letters and digits is refused", code == 1 and urls == [] and len(errors) == 1)

code, _, errors, _ = ticker("btc", status=429)
check("a refused request is one line naming the status", code == 1 and errors == ["CoinGecko answered 429."])

for status in (401, 403):
    code, _, errors, _ = ticker("btc", status=status)
    check(f"a refused key ({status}) says which key", code == 1 and errors == ["CoinGecko refused the Demo key. Replace or remove it in Apps, Market Data."])

code, _, errors, _ = ticker("btc", status=429, html=True)
check("a refusal with a page for a body still names the status", code == 1 and errors == ["CoinGecko answered 429."])

code, _, errors, _ = ticker("btc", throw="ENOTFOUND")
check("a network failure is one line", code == 1 and errors == ["Could not reach CoinGecko: ENOTFOUND."])

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
