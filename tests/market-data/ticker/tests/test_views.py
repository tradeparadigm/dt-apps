"""/ticker's three replies through DIME Terminal's relay, with fetch stubbed.

The relay answers each endpoint from fixtures/cmc-answers.json, except the
ones a case refuses. Each reply is a plain line, a blank line, then a spec the
terminal draws; the checks read the spec the way the terminal's catalog does.
"""

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parents[3] / "apps/market-data/skills/ticker/scripts/command.mjs"
ANSWERS = json.loads((HERE / "fixtures/cmc-answers.json").read_text())

STUB = """
const answers = JSON.parse(process.env.ANSWERS);
const refused = JSON.parse(process.env.REFUSED);
globalThis.fetch = async (url) => {
  console.error("URL " + url);
  const path = new URL(url).pathname.replace(/^\\/api\\/cmc\\//, "");
  if (refused.includes(path) || !(path in answers)) return new Response("{}", { status: 401 });
  return new Response(JSON.stringify(answers[path]), { status: 200 });
};
"""

JEV = "http://127.0.0.1:8091/api/jev"

# What each catalog component requires, as its reader in the terminal does.
REQUIRED = {
    "metric_card": {"label": str, "value": str},
    "labeled_output": {"label": str, "value": str},
    "data_table": {"columns": list, "rows": list},
    "performance_chart": {"label": str},
    "markdown": {"content": str},
    "alert_banner": {"variant": str, "message": str},
}

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def ticker(typed, refused=(), answers=ANSWERS, stderr=False):
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + STUB, str(SCRIPT), typed],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TERMINAL_JEV_URL": JEV,
             "ANSWERS": json.dumps(answers), "REFUSED": json.dumps(list(refused))},
    )
    urls = [line[4:] for line in result.stderr.splitlines() if line.startswith("URL ")]
    if stderr:
        return result.returncode, result.stdout, result.stderr
    return result.returncode, result.stdout, urls


def split(out):
    """The plain line and the spec, found as the terminal finds it: from the first brace."""
    start = out.index("{")
    return out[:start].strip(), json.loads(out[start:])


def valid(node):
    """A layout of layouts and components, each with the props its reader requires."""
    if "component" not in node:
        return node.get("layout") in ("stack", "grid") and all(valid(c) for c in node["children"])
    props = node.get("props", {})
    need = REQUIRED.get(node["component"])
    if need is None or any(not isinstance(props.get(k), t) or props.get(k) == "" for k, t in need.items()):
        return False
    if node["component"] == "performance_chart":
        series = props.get("series") or [{"values": props.get("values")}]
        return all(isinstance(p["value"], (int, float)) for s in series for p in s["values"])
    if node["component"] == "data_table":
        return all(isinstance(c.get("key"), str) and isinstance(c.get("header"), str) for c in props["columns"])
    return True


def components(node):
    if "component" in node:
        return [node]
    return [c for child in node["children"] for c in components(child)]


def by(spec, name):
    return [c["props"] for c in components(spec) if c["component"] == name]


def values(spec, name):
    return {p["label"]: p["value"] for p in by(spec, name)}


def tones(spec, name):
    return {p["label"]: p.get("direction") for p in by(spec, name)}


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
check("several coins are one read", len(urls) == 1 and "/api/cmc/v3/cryptocurrency/quotes/latest?symbol=BTC%2CPEPE%2CZZZ" in urls[0])
check("several coins' spec is drawable", code == 0 and valid(spec))
table = by(spec, "data_table")[0]
check("the table has the eight columns", [c["header"] for c in table["columns"]] == [
    "Coin", "Price", "24h", "7d", "30d", "Rank", "Mkt cap", "Volume"])
check("each row is the highest-ranked coin of its symbol", table["rows"] == [
    {"coin": "BTC", "price": "$74,018", "d1": "−0.97%", "d7": "+3.1%", "d30": "−12.4%", "rank": "#1", "cap": "$1.46T", "volume": "$38.2B"},
    {"coin": "PEPE", "price": "$0.0000123", "d1": "+2.00%", "d7": "+5.0%", "d30": "−20.0%", "rank": "#40", "cap": "$5.2B", "volume": "$600M"}])
check("a symbol with no coin is a warning", by(spec, "alert_banner") == [
    {"variant": "warning", "message": "No coin found for `zzz`. The other 2 are listed above."}])
check("several coins' line", line == "BTC $74,018 · −0.97% · PEPE $0.0000123 · +2.00% · No coin found for zzz")

# One coin
code, out, urls = ticker("btc")
line, spec = split(out)
paths = sorted(u.split("/api/cmc/")[1].split("?")[0] for u in urls)
check("one coin reads its quote, then a year of history and its profile", paths == sorted([
    "v3/cryptocurrency/quotes/latest", "v3/cryptocurrency/quotes/historical", "v2/cryptocurrency/info"]))
check("the history asked for is a year of days", any("quotes/historical?id=1&count=365&interval=1d" in u for u in urls))
check("a coin with no contract asks for no on-chain check", not any("/dex/" in u for u in urls))
check("one coin's spec is drawable", code == 0 and valid(spec))
check("one coin's line", line == "Bitcoin (BTC) $74,018 · −0.97% 24h · rank #1")
check("one coin's four cards", spec["children"][0]["columns"] == 4 and values(spec, "metric_card") == {
    "BTC · #1": "$74,018", "24h": "−0.97%", "Market cap · share": "$1.46T · 58.4%", "24h volume": "$38.2B · −6.4%"})
chart = by(spec, "performance_chart")
check("the last 30 days of price are charted with an axis", len(chart) == 1 and chart[0]["yAxis"] is True
      and [p["value"] for p in chart[0]["series"][0]["values"]][:2] == [84000, 83670] and len(chart[0]["series"][0]["values"]) == 30)
check("performance, the year's high and supply are rows", values(spec, "labeled_output") == {
    "7d": "+3.1%", "30d": "−12.4%", "90d": "−21.0%", "1y": "+15.7%", "1-year high": "$126,080 · 41% below",
    "Circulating": "19.9M BTC", "Max supply": "21M BTC", "Launched": "2009"})
check("the rows are two columns", [c for c in spec["children"] if c.get("layout") == "grid" and c.get("columns") == 2] != [])
check("the profile is the first sentence and its links", by(spec, "markdown") == [{"content":
    "Bitcoin (BTC) is a decentralized digital currency with a fixed supply.\n\n[Website](https://bitcoin.org/) · [Explorer](https://mempool.space/)"}])
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

# Answers with a usable shape but not the fields a view needs.
import copy
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

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
