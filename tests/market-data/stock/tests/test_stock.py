"""/stock and its suggestions, with fetch stubbed and the clock fixed.

The SEC, Wikidata and Hyperliquid answer from fixtures/stock-answers.json,
trimmed from real answers for Microsoft on 2026-11-13. Each run starts with an
empty cache unless it is handed one.
"""

import copy
import json
import pathlib
import subprocess
import sys
import tempfile
from urllib.parse import quote

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parents[3] / "apps/market-data/skills/stock/scripts"
ANSWERS = json.loads((HERE / "fixtures/stock-answers.json").read_text())
NOW = "2026-11-13T12:00:00Z"

STUB = """
const answers = JSON.parse(process.env.ANSWERS);
const refused = JSON.parse(process.env.REFUSED);
const now = Date.parse(process.env.NOW);
Date.now = () => now;
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url);
  if (u.hostname === "news.google.com") {
    console.error("URL " + JSON.stringify({ key: "news", query: u.searchParams.get("q"), agent: null }));
    return new Response(process.env.NEWS ?? "", { status: process.env.NEWS ? 200 : 404 });
  }
  let key = u.hostname + u.pathname;
  if (u.hostname === "api.hyperliquid.xyz") {
    const body = JSON.parse(init.body);
    key += "#" + body.type;
    if (body.type === "candleSnapshot") key += ":" + body.req.interval + (body.req.coin === "xyz:MSFT" ? "" : ":none");
  }
  console.error("URL " + JSON.stringify({ key, agent: init.headers?.["User-Agent"] ?? null }));
  if (refused.includes(key) || !(key in answers)) return new Response("{}", { status: 404 });
  let answer = answers[key];
  // Wikidata knows only Microsoft here; any other CIK finds no item.
  if (u.hostname === "query.wikidata.org" && !u.searchParams.get("query").includes('"789019"')) answer = { results: { bindings: [] } };
  return new Response(JSON.stringify(answer), { status: 200 });
};
"""

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def run(script, typed, refused=(), answers=ANSWERS, cache=None, news=None):
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + quote(STUB), str(SCRIPTS / script), typed],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TMPDIR": cache or tempfile.mkdtemp(), "NOW": NOW,
             "ANSWERS": json.dumps(answers), "REFUSED": json.dumps(list(refused)), **({"NEWS": news} if news else {})},
    )
    calls = [json.loads(line[4:]) for line in result.stderr.splitlines() if line.startswith("URL ")]
    errors = [line for line in result.stderr.splitlines() if not line.startswith("URL ")]
    return result.returncode, result.stdout, calls, errors


def split(out):
    start = out.index("{")
    return out[:start].strip(), json.loads(out[start:])


def components(node):
    if "component" in node:
        return [node]
    return [c for child in node["children"] for c in components(child)]


def by(spec, name):
    return [c["props"] for c in components(spec) if c["component"] == name]


def values(spec, name):
    return {p["label"]: p["value"] for p in by(spec, name)}


def keys(calls):
    return sorted(c["key"] for c in calls)


FULL = sorted([
    "www.sec.gov/files/company_tickers.json", "data.sec.gov/submissions/CIK0000789019.json",
    "data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json", "query.wikidata.org/sparql",
    "api.hyperliquid.xyz/info#metaAndAssetCtxs", "api.hyperliquid.xyz/info#candleSnapshot:1d",
    "api.hyperliquid.xyz/info#candleSnapshot:1h"])

# One company in full
code, out, calls, _ = run("command.mjs", "msft")
line, spec = split(out)
check("one company reads its list, its SEC record and figures, Wikidata, the perp, its daily and its hourly closes, and its news",
      keys(calls) == sorted(FULL + ["news"]))
check("every request to the SEC, Wikidata and Hyperliquid names the client", all(c["agent"] for c in calls if c["key"] != "news"))
check("the news is searched by the company's name as people write it, and its ticker",
      next(c["query"] for c in calls if c["key"] == "news") == '"Microsoft" MSFT when:2d')
check("the line reads on a host without components", line == "Microsoft (MSFT) · $517.51 perp · +0.40% Thu · −1.02% since close · CEO Satya Nadella")
check("the last session is 4pm New York close to close, Wed $520.72 to Thu $522.82, and the perp moves on from Thu's close",
      values(spec, "metric_card") == {"MSFT · perp": "$517.51", "Thu session": "+0.40%", "Since Thu close": "−1.02%",
                                      "Market cap": "$3.84T"})
chart = by(spec, "performance_chart")
check("the perp's daily closes are charted, at most 30", len(chart) == 1 and 2 <= len(chart[0]["series"][0]["values"]) <= 30)
rows = values(spec, "labeled_output")
check("the company's people and facts are rows", {k: rows.get(k) for k in (
    "CEO", "Founded", "Headquarters", "Employees", "Industry", "Exchange", "Fiscal year ends")} == {
    "CEO": "Satya Nadella · since 2014", "Founded": "1975", "Headquarters": "Redmond", "Employees": "228K · 2024",
    "Industry": "Services-Prepackaged Software", "Exchange": "Nasdaq", "Fiscal year ends": "June 30"})
check("revenue, net income and P/E are the last twelve months: FY2026, plus this fiscal year's first quarter, less last year's",
      rows.get("Revenue, TTM") == "$346B" and rows.get("Net income, TTM") == "$143B" and rows.get("P/E, TTM") == "27x"
      and rows.get("Shares outstanding") == "7.43B")
check("the perp's volume, open interest and funding are rows",
      rows.get("Perp volume, 24h") == "$19.8M" and rows.get("Perp open interest") == "$28.8M" and rows.get("Funding, annualized") == "+1.3%")
markdown = [p["content"] for p in by(spec, "markdown")]
check("the board is one line, by name", markdown[0].startswith("**Board**: Catherine MacGregor, Charlie Scharf,") and "Satya Nadella" in markdown[0])
filings = [l for l in markdown[1].splitlines() if l.startswith("- ")]
check("the eight latest filings worth listing link to the SEC's archive", len(filings) == 8 and all(
    "](https://www.sec.gov/Archives/edgar/data/789019/" in l for l in filings))
check("an insider trade is named as one", any(l.startswith("- [4](") and "Insider trade" in l for l in filings))
check("the sources are linked", markdown[2] == "Sources: [SEC filings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=789019) · [Wikidata](http://www.wikidata.org/entity/Q2283)")
check("a listed perp has no banner", by(spec, "alert_banner") == [])

FEED = ("<rss><channel><item><title>Microsoft says Teams outage is fixed - Reuters</title>"
        "<link>https://news.google.com/rss/articles/abc</link><pubDate>Fri, 13 Nov 2026 10:00:00 GMT</pubDate>"
        "<source url=\"https://www.reuters.com\">Reuters</source></item></channel></rss>")
_, out, _, _ = run("command.mjs", "msft", news=FEED)
md = [p["content"] for p in by(split(out)[1], "markdown")]
check("the company's news follows its board, before its filings",
      [m[:10] for m in md] == ["**Board**:", "**News** ·", "**Recent f", "Sources: ["]
      and "- [Microsoft says Teams outage is fixed](https://news.google.com/rss/articles/abc) · Reuters · 2h" in md[1])

names = subprocess.run(["node", "--input-type=module", "-e",
    f"import {{ plainName }} from {json.dumps((SCRIPTS / 'view.mjs').as_uri())};"
    "console.log(JSON.stringify(['MICROSOFT CORP', 'Meta Platforms, Inc.', 'ASML HOLDING NV', 'Coinbase Global, Inc.', 'BERKSHIRE HATHAWAY INC /DE/'].map(plainName)))"],
    capture_output=True, text=True).stdout
check("a company's SEC name loses its legal form, and capitals become a name",
      json.loads(names) == ["Microsoft", "Meta Platforms", "Asml", "Coinbase Global", "Berkshire Hathaway"])

# What the user typed
code, out, calls, _ = run("command.mjs", "")
check("nothing typed says how to use it, with no call", code == 0 and "/stock msft" in out and calls == [])
code, out, _, errors = run("command.mjs", "msft nvda")
check("two tickers are refused", code == 1 and out == "" and "one ticker" in " ".join(errors))
code, out, calls, _ = run("command.mjs", "$MSFT")
check("a $ and capitals read as the ticker", code == 0 and split(out)[0].startswith("Microsoft (MSFT)"))
code, out, calls, _ = run("command.mjs", "zzzz")
check("a ticker the SEC does not list says so, after one read", code == 0 and out.strip() == "No US-listed company found for `zzzz`." and len(calls) == 1)

# A company with less to show
code, out, calls, _ = run("command.mjs", "brk.b")
line, spec = split(out)
check("a dot in a share class reads as the SEC's dash", line.startswith("BERKSHIRE HATHAWAY INC (BRK-B)"))
check("with no SEC record, no Wikidata item and no perp, the card still names the company and its sources",
      by(spec, "metric_card") == [] and values(spec, "labeled_output") == {}
      and [p["content"] for p in by(spec, "markdown")] == ["Sources: [SEC filings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1067983)"])
check("a stock trade.xyz lists no perp on says so", by(spec, "alert_banner") == [
    {"variant": "info", "message": "trade.xyz lists no perp on BRK-B, so there is no live price."}])

code, out, _, _ = run("command.mjs", "msft", refused=["api.hyperliquid.xyz/info#metaAndAssetCtxs"])
line, spec = split(out)
check("with Hyperliquid unreadable there is no price, and a warning says it could not be read",
      by(spec, "metric_card") == [] and by(spec, "alert_banner") == [
          {"variant": "warning", "message": "The live price could not be read from Hyperliquid. Try again in a minute."}] and "Perp volume, 24h" not in values(spec, "labeled_output")
      and values(spec, "labeled_output").get("Revenue, TTM") == "$346B")

code, out, _, _ = run("command.mjs", "msft", refused=["query.wikidata.org/sparql"])
line, spec = split(out)
rows = values(spec, "labeled_output")
check("with Wikidata unreadable the people are left out, the rest kept",
      "CEO" not in rows and not any("Board" in p["content"] for p in by(spec, "markdown")) and rows.get("Industry") == "Services-Prepackaged Software"
      and line == "MICROSOFT CORP (MSFT) · $517.51 perp · +0.40% Thu · −1.02% since close")

code, out, _, _ = run("command.mjs", "msft", refused=["data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"])
_, spec = split(out)
check("with no SEC figures there is no market cap or P/E, and the price stays",
      values(spec, "metric_card") == {"MSFT · perp": "$517.51", "Thu session": "+0.40%", "Since Thu close": "−1.02%"})

code, out, _, _ = run("command.mjs", "msft", refused=["api.hyperliquid.xyz/info#candleSnapshot:1h"])
check("with the hourly closes unreadable the price stands alone", values(split(out)[1], "metric_card") == {
    "MSFT · perp": "$517.51", "Market cap": "$3.84T"})

weekend = copy.deepcopy(ANSWERS)
weekend["api.hyperliquid.xyz/info#candleSnapshot:1h"] = [
    c for c in weekend["api.hyperliquid.xyz/info#candleSnapshot:1h"] if c["t"] + 3_600_000 != 1794517200000]
_, out, _, _ = run("command.mjs", "msft", answers=weekend)
check("with Thursday's close missing, the last session is Wednesday's", "Wed session" in values(split(out)[1], "metric_card"))

monday = copy.deepcopy(ANSWERS)
monday["api.hyperliquid.xyz/info#candleSnapshot:1h"] = [
    c for c in monday["api.hyperliquid.xyz/info#candleSnapshot:1h"] if c["t"] < 1794254400000]
_, out, _, _ = run("command.mjs", "msft", answers=monday)
check("the perp's 4pm on a Saturday or Sunday is no session", not any("session" in k for k in values(split(out)[1], "metric_card")))

code, out, _, errors = run("command.mjs", "msft", refused=["www.sec.gov/files/company_tickers.json"])
check("with the SEC's list unreadable and none cached, it says so", code == 1 and out == "" and "could not be read" in " ".join(errors))

# Share counts
def with_shares(shares):
    answers = copy.deepcopy(ANSWERS)
    answers["data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"]["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"] = shares
    return answers


classes = [
    {"end": "2026-10-22", "val": 5_000_000_000, "accn": "a2", "filed": "2026-10-28"},
    {"end": "2026-10-22", "val": 2_000_000_000, "accn": "a2", "filed": "2026-10-28"},
    {"end": "2026-10-22", "val": 5_000_000_000, "accn": "a3", "filed": "2026-11-05"},
    {"end": "2026-07-20", "val": 9_000_000_000, "accn": "a1", "filed": "2026-07-30"},
]
_, out, _, _ = run("command.mjs", "msft", answers=with_shares(classes))
check("share classes in one filing are summed, a later filing of the same date counted alone",
      values(split(out)[1], "labeled_output").get("Shares outstanding") == "5B")
_, out, _, _ = run("command.mjs", "msft", answers=with_shares(classes[:2]))
check("two classes on the latest date add up", values(split(out)[1], "labeled_output").get("Shares outstanding") == "7B")
_, out, _, _ = run("command.mjs", "msft", answers=with_shares([{"end": "2011-04-29", "val": 941481, "accn": "old", "filed": "2011-05-06"}]))
_, spec = split(out)
check("a share count over a year old is left out, and with it market cap and P/E",
      "Shares outstanding" not in values(spec, "labeled_output") and "Market cap" not in values(spec, "metric_card"))

# Annual figures
quarter = copy.deepcopy(ANSWERS)
facts = quarter["data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"]["facts"]["us-gaap"]["NetIncomeLoss"]["units"]
facts["USD"] = [f for f in facts["USD"] if f["end"] <= "2026-06-30"]
facts["USD"].insert(0, {"start": "2026-04-01", "end": "2026-06-30", "val": 27_000_000_000, "form": "10-K", "accn": "q4"})
_, out, _, _ = run("command.mjs", "msft", answers=quarter)
rows = values(split(out)[1], "labeled_output")
check("with no quarter filed since the annual report, the fiscal year stands, never its fourth quarter",
      rows.get("Net income, FY2026") == "$134B" and rows.get("P/E, FY2026") == "29x")

half = copy.deepcopy(ANSWERS)
facts = half["data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"]["facts"]["us-gaap"]["NetIncomeLoss"]["units"]
three_months = next(f for f in facts["USD"] if f["start"] == "2025-10-01" and f["end"] == "2025-12-31")
facts["USD"].remove(three_months)
facts["USD"].insert(0, three_months)
facts["USD"].append({"start": "2026-07-01", "end": "2026-12-31", "val": 80_000_000_000, "form": "10-Q", "accn": "h1"})
_, out, _, _ = run("command.mjs", "msft", answers=half)
check("half a year in, the twelve months take away last year's half, never its second quarter alone",
      values(split(out)[1], "labeled_output").get("Net income, TTM") == "$148B")

# Filings
unlisted = copy.deepcopy(ANSWERS)
recent = unlisted["data.sec.gov/submissions/CIK0000789019.json"]["filings"]["recent"]
for k, v in {"form": "S-8", "filingDate": "2026-11-13", "accessionNumber": "0000789019-26-999999", "primaryDocument": "s8.htm"}.items():
    recent[k].insert(0, v)
_, out, _, _ = run("command.mjs", "msft", answers=unlisted)
check("a filing type the card does not list is skipped", not any("s8.htm" in p["content"] for p in by(split(out)[1], "markdown")))

# Suggestions
cache = tempfile.mkdtemp()
code, out, calls, _ = run("lookup.mjs", "", cache=cache)
rows = json.loads(out)["rows"]
check("nothing typed lists the popular companies the SEC has", code == 0 and [r["id"] for r in rows] == ["nvda", "msft", "coin"])
check("a row runs /stock with its ticker", rows[1] == {"id": "msft", "lead": {"text": "MSFT", "tone": "accent"},
                                                     "main": "MICROSOFT CORP", "run": "/stock msft"})
code, out, calls, _ = run("lookup.mjs", "ms", cache=cache)
check("the exact ticker comes first, then tickers that start with it in the SEC's order, and the list is read once a day",
      [r["id"] for r in json.loads(out)["rows"]] == ["ms", "msft", "mstr"] and calls == [])
code, out, calls, _ = run("lookup.mjs", "berk", cache=cache)
check("a company's name matches too", [r["id"] for r in json.loads(out)["rows"]] == ["brk-b"])

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
