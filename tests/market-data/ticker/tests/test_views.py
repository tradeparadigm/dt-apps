"""/ticker's three replies through DIME Terminal's relay, with fetch stubbed.

The relay answers each endpoint from fixtures/cmc-answers.json, except the
ones a case refuses. Each reply is a plain line, a blank line, then a spec the
terminal draws; the checks read the spec the way the terminal's catalog does.
"""

import copy
import json
import pathlib
import subprocess
import sys
import tempfile
from urllib.parse import unquote

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from spec import by, components, split, tones, valid, values  # noqa: E402
SCRIPT = HERE.parents[3] / "apps/market-data/skills/ticker/scripts/command.mjs"
ANSWERS = json.loads((HERE / "fixtures/cmc-answers.json").read_text())
LLAMA = json.loads((HERE / "fixtures/llama-answers.json").read_text())
NOW = "2026-11-13T08:00:00Z"

# A hung read holds an interval open, as a real socket keeps node running.
STUB = """
const answers = JSON.parse(process.env.ANSWERS);
const refused = JSON.parse(process.env.REFUSED);
const llama = JSON.parse(process.env.LLAMA);
const now = Date.parse(process.env.NOW);
Date.now = () => now;
const hang = process.env.HANG === "1";
globalThis.fetch = async (url, init = {}) => {
  console.error("URL " + url);
  const u = new URL(url);
  if (u.hostname.endsWith("llama.fi") && hang) {
    const socket = setInterval(() => {}, 1000);
    return new Promise((_, reject) => init.signal?.addEventListener("abort", () => { clearInterval(socket); reject(init.signal.reason); }));
  }
  if (u.hostname.endsWith("llama.fi")) {
    const type = u.searchParams.get("dataType");
    const key = u.hostname + decodeURIComponent(u.pathname) + (type ? `?dataType=${type}` : "");
    if (refused.includes(key) || !(key in llama)) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(llama[key]), { status: 200 });
  }
  const path = u.pathname.replace(/^\\/api\\/cmc\\//, "");
  if (refused.includes(path) || !(path in answers)) return new Response("{}", { status: 401 });
  return new Response(JSON.stringify(answers[path]), { status: 200 });
};
"""

JEV = "http://127.0.0.1:8091/api/jev"

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def ticker(typed, refused=(), answers=ANSWERS, stderr=False, llama=LLAMA, cache=None, hang=False):
    """Each run starts with an empty cache unless it is handed one."""
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + STUB, str(SCRIPT), typed],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TERMINAL_JEV_URL": JEV, "TMPDIR": cache or tempfile.mkdtemp(), "NOW": NOW,
             "ANSWERS": json.dumps(answers), "REFUSED": json.dumps(list(refused)), "LLAMA": json.dumps(llama),
             "HANG": "1" if hang else ""},
    )
    urls = [line[4:] for line in result.stderr.splitlines() if line.startswith("URL ")]
    if stderr:
        return result.returncode, result.stdout, result.stderr
    return result.returncode, result.stdout, urls


# The market
code, out, urls = ticker("")
line, spec = split(out)
check("the market reads each of its endpoints once", sorted(u.split("/api/cmc/")[1].split("?")[0] for u in urls) == sorted([
    "v1/global-metrics/quotes/latest", "v3/fear-and-greed/latest", "v3/fear-and-greed/historical",
    "v1/altcoin-season-index/latest", "v3/index/cmc100-latest", "v3/index/cmc20-latest"]))
check("the market's spec is drawable", code == 0 and valid(spec))
check("the market's line reads on a host without components",
      line == "Market cap $2.61T (+1.2%) · 24h volume $98.4B · Fear & Greed 62 Greed · BTC 58.4%")
cards = values(spec, "metric_card")
check("the market shows its six cards in a 3-column grid",
      spec["children"][0]["layout"] == "grid" and spec["children"][0]["columns"] == 3 and list(cards) == [
          "Total market cap", "24h volume", "Fear & Greed", "BTC dominance", "ETH dominance", "Altcoin season"])
check("each card has its figure and change", cards == {
    "Total market cap": "$2.61T · +1.2%", "24h volume": "$98.4B · −8.1%", "Fear & Greed": "62 · Greed",
    "BTC dominance": "58.4% · −0.3 pts", "ETH dominance": "11.9% · +0.2 pts", "Altcoin season": "34 / 100 · Mixed"})
check("each card's colour follows its change", tones(spec, "metric_card") == {
    "Total market cap": "up", "24h volume": "down", "Fear & Greed": "up",
    "BTC dominance": "down", "ETH dominance": "up", "Altcoin season": None})
check("both indexes are rows", values(spec, "labeled_output") == {
    "Top-100 index": "162.4 · +0.9% 24h", "Top-20 index": "1,288.2 · +1.1% 24h"})
chart = by(spec, "performance_chart")
points = chart[0]["series"][0]["values"] if chart else []
check("Fear & Greed's 30 days are charted oldest first",
      len(points) == 30 and points[-1]["value"] == 62 and points[0]["name"] == "Oct 15" and points[-1]["name"] == "Nov 13")

code, out, _ = ticker("", refused=["v3/fear-and-greed/latest", "v3/fear-and-greed/historical", "v3/index/cmc20-latest"])
_, spec = split(out)
check("a section whose read fails is left out, the rest kept",
      code == 0 and valid(spec) and "Fear & Greed" not in values(spec, "metric_card")
      and list(values(spec, "labeled_output")) == ["Top-100 index"] and by(spec, "performance_chart") == [])

code, out, _ = ticker("", refused=["v1/global-metrics/quotes/latest"])
check("with no global figures the market says it could not be read",
      code == 1 and out == "")

# Several coins
code, out, urls = ticker("btc pepe zzz")
line, spec = split(out)
MIDNIGHT = "2026-11-13T00%3A00%3A00.000Z"
check("several coins are one read of quotes, then one of every coin's month of closes to today's 00:00 UTC",
      len(urls) == 2 and "/api/cmc/v3/cryptocurrency/quotes/latest?symbol=BTC%2CPEPE%2CZZZ" in urls[0]
      and f"quotes/historical?id=1%2C24478&count=31&interval=daily&time_end={MIDNIGHT}" in urls[1])
check("several coins' spec is drawable", code == 0 and valid(spec))
table = by(spec, "data_table")[0]
check("the table has the eight columns, the day's since 00:00 UTC", [c["header"] for c in table["columns"]] == [
    "Coin", "Price", "Today", "7d", "30d", "Rank", "Mkt cap", "Volume"])
check("each row is the highest-ranked coin of its symbol, its changes from its daily closes, or CoinMarketCap's rolling ones with none", table["rows"] == [
    {"coin": "BTC", "price": "$74,018", "d1": "−0.55%", "d7": "−3.5%", "d30": "−38.7%", "rank": "#1", "cap": "$1.46T", "volume": "$38.2B"},
    {"coin": "PEPE", "price": "$0.0000123", "d1": None, "d7": "+5.0%", "d30": "−20.0%", "rank": "#40", "cap": "$5.2B", "volume": "$600M"}])
check("a symbol with no coin is a warning", by(spec, "alert_banner") == [
    {"variant": "warning", "message": "No coin found for `zzz`. The other 2 are listed above."}])
check("several coins' line", line == "BTC $74,018 · −0.55% · PEPE $0.0000123 · No coin found for zzz")

# One coin
code, out, urls = ticker("btc")
line, spec = split(out)
paths = sorted(u.split("/api/cmc/")[1].split("?")[0] for u in urls if "/api/cmc/" in u)
check("one coin reads its quote, then a year of history and its profile", paths == sorted([
    "v3/cryptocurrency/quotes/latest", "v3/cryptocurrency/quotes/historical", "v2/cryptocurrency/info"]))
check("the history asked for is a year of daily closes to today's 00:00 UTC",
      any(f"quotes/historical?id=1&count=366&interval=daily&time_end={MIDNIGHT}" in u for u in urls))
check("a coin with no contract asks for no on-chain check", not any("/dex/" in u for u in urls))
check("one coin's spec is drawable", code == 0 and valid(spec))
check("one coin's line", line == "Bitcoin (BTC) $74,018 · −0.55% today · rank #1")
check("one coin's four cards", spec["children"][0]["columns"] == 4 and values(spec, "metric_card") == {
    "BTC · #1": "$74,018", "Today": "−0.55%", "Market cap · share": "$1.46T · 58.4%", "24h volume": "$38.2B · −6.4%"})
chart = by(spec, "performance_chart")
check("the last 30 days of price are charted with an axis", len(chart) == 1 and chart[0]["yAxis"] is True
      and [p["value"] for p in chart[0]["series"][0]["values"]][:2] == [84000, 83670] and len(chart[0]["series"][0]["values"]) == 30)
check("performance, the year's high and supply are rows", values(spec, "labeled_output") == {
    "7d": "−3.5%", "30d": "−38.7%", "90d": "−33.1%", "1y": "+15.7%", "1-year high": "$126,080 · 41% below",
    "Circulating": "19.9M BTC", "Max supply": "21M BTC", "Launched": "2009",
    "TVL on Bitcoin": "$3.61B", "DEX volume, 24h": "$815K", "Fees, 30d": "$1.31M", "Revenue, 1y": "$376K",
    "Mkt cap ÷ revenue": "over 1,000x"})
check("the rows are two columns", [c for c in spec["children"] if c.get("layout") == "grid" and c.get("columns") == 2] != [])
check("the on-chain rows name their source, then the profile is the first sentence and its links", by(spec, "markdown") == [
    {"content": "On-chain, from [DefiLlama](https://defillama.com/chain/Bitcoin)"},
    {"content": "Bitcoin (BTC) is a decentralized digital currency with a fixed supply.\n\n[Website](https://bitcoin.org/) · [Explorer](https://mempool.space/)"}])
check("a coin with no contract has no on-chain banner", by(spec, "alert_banner") == [])

code, out, urls = ticker("pepe")
_, spec = split(out)
check("a token reads its contract's check",
      sum("/dex/" in u for u in urls) == 1 and any("security/detail?platformName=ethereum&address=0x" in u for u in urls))
check("a token's check is a banner naming what it flagged", by(spec, "alert_banner") == [
    {"variant": "info", "message": "Contract check: safe · flagged: Blacklist Restrictions Found · buy tax 0%, sell tax 0%"}])

history = ANSWERS["v3/cryptocurrency/quotes/historical"]["data"]
history["24478"] = history["1"]
code, out, _ = ticker("pepe")
_, spec = split(out)
chart = by(spec, "performance_chart")
check("a sub-dollar price is charted with no axis", len(chart) == 1 and chart[0]["yAxis"] is False)
del history["24478"]

year = ANSWERS["v3/cryptocurrency/quotes/historical"]["data"]["1"]["quotes"]
ANSWERS["v3/cryptocurrency/quotes/historical"]["data"]["1"]["quotes"] = year[-100:]
code, out, _ = ticker("btc")
_, spec = split(out)
rows = values(spec, "labeled_output")
check("a coin with under a year of history has no 1y figures, and still its chart",
      "1y" not in rows and "1-year high" not in rows and "30d" in rows and len(by(spec, "performance_chart")) == 1)
ANSWERS["v3/cryptocurrency/quotes/historical"]["data"]["1"]["quotes"] = year

ANSWERS["v1/dex/security/detail"]["data"][0]["securityItems"][2]["isHit"] = True
code, out, _ = ticker("pepe")
_, spec = split(out)
check("a severe flag makes the banner a warning", by(spec, "alert_banner")[0]["variant"] == "warning")

code, out, _ = ticker("pepe", refused=["v1/dex/security/detail", "v3/cryptocurrency/quotes/historical", "v2/cryptocurrency/info"])
_, spec = split(out)
check("a check, history or profile that cannot be read is left out",
      code == 0 and valid(spec) and by(spec, "alert_banner") == [] and by(spec, "performance_chart") == [] and by(spec, "markdown") == [])

code, out, _ = ticker("zzz")
check("one symbol with no coin says so in a line", code == 0 and out.strip() == "No coin found for `zzz`.")

code, out, urls = ticker("zzz yyy")
check("a table of symbols with no coin reads no history", len(urls) == 1 and "quotes/latest" in urls[0])
zero = copy.deepcopy(ANSWERS)
zero["v3/cryptocurrency/quotes/historical"]["data"]["1"]["quotes"][-8]["quote"]["USD"]["price"] = 0
code, out, _ = ticker("btc", answers=zero)
check("a close of zero gives no change for its period", "7d" not in values(split(out)[1], "labeled_output"))

late = copy.deepcopy(ANSWERS)
late["v3/cryptocurrency/quotes/historical"]["data"]["1"]["quotes"].pop()
code, out, _ = ticker("btc", answers=late)
line, spec = split(out)
check("before CoinMarketCap publishes today's 00:00 close, the card shows the rolling 24h and periods, labelled so",
      line == "Bitcoin (BTC) $74,018 · −0.97% 24h · rank #1"
      and {k: values(spec, "labeled_output").get(k) for k in ("7d", "30d", "90d")} == {"7d": "+3.1%", "30d": "−12.4%", "90d": "−21.0%"})
gap = copy.deepcopy(ANSWERS)
del gap["v3/cryptocurrency/quotes/historical"]["data"]["1"]["quotes"][-8]
code, out, _ = ticker("btc", answers=gap)
rows = values(split(out)[1], "labeled_output")
check("a missing day's close leaves that period out, and never shifts the others onto the wrong day",
      "7d" not in rows and rows.get("30d") == "−38.7%" and values(split(out)[1], "metric_card").get("Today") == "−0.55%")
code, out, _ = ticker("btc pepe", answers=late)
table = by(split(out)[1], "data_table")[0]
check("with no coin's 00:00 close yet, the table's day column is the rolling 24h",
      [c["header"] for c in table["columns"]][2] == "24h" and [r["d1"] for r in table["rows"]] == ["−0.97%", "+2.00%"])

code, out, _ = ticker("btc", refused=["v3/cryptocurrency/quotes/historical"])
line, spec = split(out)
check("with no daily closes the card shows CoinMarketCap's rolling 24h and periods, labelled so",
      line == "Bitcoin (BTC) $74,018 · −0.97% 24h · rank #1"
      and values(spec, "metric_card").get("24h") == "−0.97%" and "Today" not in values(spec, "metric_card")
      and {k: values(spec, "labeled_output").get(k) for k in ("7d", "30d", "90d")} == {"7d": "+3.1%", "30d": "−12.4%", "90d": "−21.0%"})
code, out, _ = ticker("btc pepe", refused=["v3/cryptocurrency/quotes/historical"])
table = by(split(out)[1], "data_table")[0]
check("with no daily closes the table heads its day's column 24h", [c["header"] for c in table["columns"]][2] == "24h"
      and table["rows"][0]["d1"] == "−0.97%")

# Answers with a usable shape but not the fields a view needs.
odd = copy.deepcopy(ANSWERS)
odd["v1/global-metrics/quotes/latest"]["data"]["quote"] = {"EUR": {}}
code, out, err = ticker("", answers=odd, stderr=True)
check("market totals with no USD figures read as unavailable, not a crash",
      code == 1 and "could not be read" in err and "TypeError" not in err)

linked = copy.deepcopy(ANSWERS)
linked["v2/cryptocurrency/info"]["data"]["1"]["urls"]["website"] = ["javascript:alert(1)", "http://plain.example", "https://bitcoin.org"]
code, out, _ = ticker("btc", answers=linked)
about = " ".join(p["content"] for p in by(split(out)[1], "markdown")) if code == 0 else ""
check("only an https link reaches the card", "https://bitcoin.org" in about and "javascript:" not in about and "http://plain" not in about)

# Two coins share PEPE: the better-ranked one is the card, in either order.
for order in ("as listed", "reversed"):
    shared = copy.deepcopy(ANSWERS)
    if order == "reversed":
        shared["v3/cryptocurrency/quotes/latest"]["data"].reverse()
    code, out, _ = ticker("pepe", answers=shared)
    check(f"a shared symbol shows its better-ranked coin ({order})", code == 0 and split(out)[0].startswith("Pepe (PEPE)"))

# On-chain, from DefiLlama
def with_coin(cid, symbol, slug, name, cap, tags=None, website=None):
    """The quotes answer with one more coin, priced like BTC, and its website in its profile."""
    answers = copy.deepcopy(ANSWERS)
    coin = copy.deepcopy(answers["v3/cryptocurrency/quotes/latest"]["data"][0])
    coin.update({"id": cid, "symbol": symbol, "slug": slug, "name": name, "cmc_rank": 20, "platform": None})
    coin["quote"][0]["market_cap"] = cap
    if tags is not None:
        coin["tags"] = tags
    answers["v3/cryptocurrency/quotes/latest"]["data"].append(coin)
    if website:
        answers["v2/cryptocurrency/info"]["data"][str(cid)] = {"urls": {"website": [website]}}
    return answers


def on_chain(typed, **kw):
    code, out, urls = ticker(typed, **kw)
    _, spec = split(out)
    source = [m["content"] for m in by(spec, "markdown") if "DefiLlama" in m["content"]]
    llama = sorted(unquote(u.split("llama.fi")[1].split("?")[0]) for u in urls if "llama.fi" in u)
    return code, spec, source, llama


INDEX = ["/config", "/stablecoins", "/v2/chains"]

code, spec, source, llama = on_chain("btc")
check("a chain's coin reads its chain, never the protocol of its name",
      llama == sorted(INDEX + ["/v2/chains", "/stablecoinchains", "/overview/dexs/Bitcoin", "/overview/fees/Bitcoin", "/overview/fees/Bitcoin"]))

code, spec, source, llama = on_chain("aave", answers=with_coin(7278, "AAVE", "aave", "Aave", 1.7e9))
check("a protocol's token shows TVL, fees, revenue and its multiple", code == 0 and valid(spec) and {
    k: v for k, v in values(spec, "labeled_output").items() if k in ("TVL", "Fees, 30d", "Revenue, 1y", "Mkt cap ÷ revenue")} == {
    "TVL": "$15.6B", "Fees, 30d": "$39.6M", "Revenue, 1y": "$87.1M", "Mkt cap ÷ revenue": "20x"})
check("a protocol's section links its page", source == ["On-chain, from [DefiLlama](https://defillama.com/protocol/aave)"])
check("a protocol with no chain reads no chain figures", not any(u.startswith(("/overview/", "/stablecoinchains")) for u in llama))

code, spec, source, llama = on_chain("hype", answers=with_coin(32196, "HYPE", "hyperliquid", "Hyperliquid", 1.69e10))
rows = values(spec, "labeled_output")
check("a coin that is both a chain and a protocol shows the chain, its revenue and the multiple on it", all(rows.get(k) == v for k, v in {
    "TVL on Hyperliquid L1": "$2.3B", "Stablecoins on Hyperliquid L1": "$6.35B", "DEX volume, 24h": "$269M",
    "Fees, 30d": "$162M", "Revenue, 1y": "$723M", "Mkt cap ÷ revenue": "23x"}.items()) and "TVL" not in rows)
check("the protocol of a chain's coin is not read", not any(u.startswith(("/summary/", "/tvl/")) for u in llama))

code, spec, source, llama = on_chain("arb", answers=with_coin(11841, "ARB", "arbitrum", "Arbitrum", 1.2e9))
rows = values(spec, "labeled_output")
check("a chain's coin shows the chain's fees, never its foundation's", rows.get("Fees, 30d") == "$21.3M" and rows.get("TVL on Arbitrum") == "$1.34B"
      and source == ["On-chain, from [DefiLlama](https://defillama.com/chain/Arbitrum)"])

code, spec, source, llama = on_chain("lunc", answers=with_coin(4172, "LUNC", "terra-luna", "Terra Classic", 3e8))
check("of two chains sharing a coin's id, the one with its slug is shown", "TVL on Terra Classic" in values(spec, "labeled_output"))

code, spec, source, llama = on_chain("bnb", answers=with_coin(1839, "BNB", "bnb", "BNB", 9e10))
check("of two chains sharing a coin, the one holding more is shown",
      values(spec, "labeled_output").get("TVL on BSC") == "$4.76B" and "/overview/dexs/BSC" in llama)

tagged = with_coin(825, "USDT", "tether", "Tether", 1.75e11, tags=[{"slug": "stablecoin", "name": "Stablecoin"}])
code, spec, source, llama = on_chain("usdt", answers=tagged)
rows = values(spec, "labeled_output")
check("a stablecoin shows its supply, its month's change and its chains",
      rows.get("Supply") == "$175B" and rows.get("Supply, 30d") == "−4.7%" and rows.get("Chains") == "3"
      and source == ["On-chain, from [DefiLlama](https://defillama.com/stablecoin/tether)"])

busd = with_coin(4687, "BUSD", "binance-usd", "BUSD", 3.4e7, tags=[{"slug": "stablecoin"}])
code, spec, source, llama = on_chain("busd", answers=busd)
check("of stablecoins sharing a symbol, the one with the coin's slug is shown, linked by DefiLlama's slug of its name",
      values(spec, "labeled_output").get("Supply") == "$34.3M" and source == ["On-chain, from [DefiLlama](https://defillama.com/stablecoin/binance-usd)"])
busd["v3/cryptocurrency/quotes/latest"]["data"][-1]["slug"] = "busd"
code, spec, source, llama = on_chain("busd", answers=busd)
check("with no slug match, a stablecoin matches by symbol", values(spec, "labeled_output").get("Supply") == "$233M")

code, spec, source, llama = on_chain("usdt", answers=with_coin(825, "USDT", "tether", "Tether", 1.75e11, tags=[]))
check("a coin CoinMarketCap does not tag a stablecoin is not read as one", source == [] and "Supply" not in values(spec, "labeled_output"))

code, spec, source, llama = on_chain("btc", llama={**LLAMA, "api.llama.fi/v2/chains": [c for c in LLAMA["api.llama.fi/v2/chains"] if c["name"] != "Bitcoin"]})
check("a protocol with no fee data is not shown", source == [] and llama == INDEX)

morpho = with_coin(34104, "MORPHO", "morpho", "Morpho", 1e9, website="https://morpho.org/")
code, spec, source, llama = on_chain("morpho", answers=morpho)
check("a protocol with no CoinMarketCap id matches by slug, symbol and website",
      source == ["On-chain, from [DefiLlama](https://defillama.com/protocol/morpho)"] and values(spec, "labeled_output").get("TVL") == "$10.6B")
code, spec, source, llama = on_chain("kite", answers=with_coin(38828, "KITE", "kite", "Kite", 5e8, website="https://gokite.ai/"))
check("a slug and symbol on another project's website do not match", source == [] and llama == INDEX)
code, spec, source, llama = on_chain("morpho", answers=with_coin(34104, "MORPHO", "morpho", "Morpho", 1e9))
check("with no website to compare, a slug does not match a protocol", source == [])
code, spec, source, llama = on_chain("lit", answers=with_coin(38000, "LIT", "lighter", "Lighter", 5e8, website="https://lighter.xyz"))
check("a protocol that is no parent matches by slug, symbol and the same domain", source == ["On-chain, from [DefiLlama](https://defillama.com/protocol/lighter)"])
code, spec, source, llama = on_chain("nos", answers=with_coin(38001, "NOS", "nosite", "No Site", 5e7))
check("a protocol DefiLlama gives no website never matches a coin with none by slug", source == [] and llama == INDEX)
code, spec, source, llama = on_chain("okb", answers=with_coin(3897, "OKB", "okb", "OKB", 1e9))
check("a chain DefiLlama gives no symbol matches by slug", source == ["On-chain, from [DefiLlama](https://defillama.com/chain/X%20Layer)"])
code, spec, source, llama = on_chain("other", answers=with_coin(38002, "OTHER", "fakecoin", "Other", 1e7))
check("a chain whose symbol is another coin's does not match by slug", source == [])
code, spec, source, llama = on_chain("zora", answers=with_coin(35931, "ZORA", "zora", "ZORA", 5e7))
check("a chain with no symbol whose gas is another coin's leaves the coin to its protocol",
      source == ["On-chain, from [DefiLlama](https://defillama.com/protocol/zora-protocol)"])
code, spec, source, llama = on_chain("sym", answers=with_coin(38003, "SYM", "symcoin", "Sym", 1e7))
check("a chain with the coin's symbol matches by slug", source == ["On-chain, from [DefiLlama](https://defillama.com/chain/Sym%20Chain)"])
code, spec, source, llama = on_chain("twx", answers=with_coin(38004, "TWX", "twinsite", "Twin", 1e7, website="https://twin.site"))
check("a protocol on the coin's website with another symbol does not match", source == [] and llama == INDEX)
noslug = with_coin(38005, "NOS", None, "No Slug", 1e7)
code, spec, source, llama = on_chain("nos", answers=noslug)
check("a coin with no slug matches nothing by slug", source == [] and llama == INDEX)

code, spec, source, llama = on_chain("pepe")
check("a coin DefiLlama does not track has no section and reads only the index", source == [] and llama == INDEX)

code, spec, source, llama = on_chain("btc", refused=["api.llama.fi/config"])
check("with DefiLlama unreadable the card is drawn without the section",
      code == 0 and valid(spec) and source == [] and "TVL on Bitcoin" not in values(spec, "labeled_output"))

code, spec, source, llama = on_chain("btc", refused=["api.llama.fi/overview/dexs/Bitcoin"])
rows = values(spec, "labeled_output")
check("a figure that cannot be read is left out, the rest kept", "DEX volume, 24h" not in rows and rows.get("TVL on Bitcoin") == "$3.61B")

cache = tempfile.mkdtemp()
on_chain("btc", cache=cache)
code, spec, source, llama = on_chain("btc", cache=cache)
check("the index is read once a day, and the figures every time",
      "/config" not in llama and "/overview/dexs/Bitcoin" in llama and values(spec, "labeled_output").get("TVL on Bitcoin") == "$3.61B")

index = pathlib.Path(cache) / "market-data-ticker-lookup/llama-index.json"
aged = json.loads(index.read_text())
aged["at"] -= 25 * 3_600_000
index.write_text(json.dumps(aged))
code, spec, source, llama = on_chain("btc", cache=cache, refused=["api.llama.fi/config"])
check("a failed refresh of a day-old index answers from the old one", "/config" in llama and source != [])
code, spec, source, llama = on_chain("btc", cache=cache, refused=["api.llama.fi/config"])
check("after a failed refresh the old index serves another day, with no new attempt", "/config" not in llama and source != [])

import time
started = time.monotonic()
code, spec, source, llama = on_chain("btc", hang=True)
took = time.monotonic() - started
check("a DefiLlama that never answers holds the card about 5 s, and the card is drawn without it",
      code == 0 and valid(spec) and source == [] and 4.5 < took < 8)

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
