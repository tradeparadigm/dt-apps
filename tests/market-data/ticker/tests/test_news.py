"""The news section: Google News' RSS search, with fetch stubbed and the clock fixed.

The feed is written by hand, one item per rule the section applies, in the
shape Google sends: a title ending " - <outlet>", a link, a pubDate, a source.
"""

import json
import pathlib
import subprocess
import sys
from urllib.parse import parse_qs, quote, urlparse

HERE = pathlib.Path(__file__).resolve().parent
NEWS = HERE.parents[3] / "apps/market-data/skills/ticker/scripts/news.mjs"
NOW = "2026-11-13T08:00:00Z"


def item(title, source, hours_ago, link=None):
    at = f"Fri, 13 Nov 2026 {8 - hours_ago:02d}:00:00 GMT" if hours_ago <= 8 else "Wed, 11 Nov 2026 08:00:00 GMT"
    link = link or f"https://news.google.com/rss/articles/{abs(hash(title)) % 10**8}"
    return (f"<item><title>{title} - {source}</title><link>{link}</link><pubDate>{at}</pubDate>"
            f"<source url=\"https://example.com\">{source}</source></item>")


FEED = "<rss><channel><title>Google News</title>" + "".join([
    item("Nvidia Announces CEO Transition", "NVIDIA Newsroom", 7),
    item("Nvidia Stock Price Prediction 2027: Can It Recover?", "Coinpedia", 6),
    item("Retail buyers limit Nvidia stock rout", "Reuters", 6),
    item("Retail buyers limit Nvidia stock rout as shares slide", "Investing.com", 5),
    item("Chipmakers fall as NVDA leads the Nasdaq lower", "CNBC", 4),
    item("Apple hits a record high", "Barron's", 4),
    item("Nvidia's board names an interim CEO", "WSJ", 3, link="http://plain.example/story"),
    item("Nvidia &amp; the &quot;succession&quot; question", "<![CDATA[The Verge]]>", 3),
    item("What [Nvidia] means for AI", "Forbes", 2),
    item("Is Nvidia undervalued?", "The Motley Fool", 1),
    item("Nvidia, two days on", "Bloomberg", 48),
]) + "</channel></rss>"

STUB = """
const now = Date.parse(process.env.NOW);
Date.now = () => now;
globalThis.fetch = async (url) => {
  console.error("URL " + url);
  if (process.env.REFUSE) return new Response("", { status: 503 });
  return new Response(process.env.FEED, { status: 200 });
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


def news(name, symbol, feed=FEED, refuse=False):
    script = f"""
import {{ headlines, newsMarkdown }} from {json.dumps(NEWS.as_uri())};
const list = await headlines({json.dumps(name)}, {json.dumps(symbol)});
console.log(JSON.stringify({{ list, markdown: newsMarkdown(list, {json.dumps(name)}, {json.dumps(symbol)}) }}));
"""
    result = subprocess.run(
        ["node", "--import", "data:text/javascript," + quote(STUB), "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "NOW": NOW, "FEED": feed, "REFUSE": "1" if refuse else ""},
    )
    urls = [line[4:] for line in result.stderr.splitlines() if line.startswith("URL ")]
    return json.loads(result.stdout), urls


out, urls = news("Nvidia", "nvda")
query = parse_qs(urlparse(urls[0]).query)
check("one search, for the quoted name and the symbol over two days, in US English",
      len(urls) == 1 and query["q"] == ['"Nvidia" NVDA when:2d'] and query["hl"] == ["en-US"] and query["gl"] == ["US"])
titles = [i["title"] for i in out["list"]]
check("five headlines at most, in Google's order", titles == [
    "Nvidia Announces CEO Transition", "Retail buyers limit Nvidia stock rout",
    "Chipmakers fall as NVDA leads the Nasdaq lower", 'Nvidia & the "succession" question', "What [Nvidia] means for AI"])
check("a forecast is not news", not any("Prediction" in t for t in titles))
check("a second outlet's run of one story is dropped", "Retail buyers limit Nvidia stock rout as shares slide" not in titles)
check("a headline naming neither the company nor its symbol is dropped", "Apple hits a record high" not in titles)
check("only an https link is kept", "Nvidia's board names an interim CEO" not in titles)
check("the outlet comes off the title and the XML is unescaped", out["list"][3]["source"] == "The Verge")
md = out["markdown"].splitlines()
check("the section heads with a link to the same search", md[0] == "**News** · from [Google News](https://news.google.com/search?q=%22Nvidia%22+NVDA)")
check("each line is the headline linked, its outlet and its age",
      md[2].startswith("- [Nvidia Announces CEO Transition](https://news.google.com/rss/articles/") and md[2].endswith(") · NVIDIA Newsroom · 7h"))
check("brackets come out of a headline so the link stays whole", "- [What Nvidia means for AI](" in out["markdown"])

coins = "<rss><channel>" + item("BTC slips below $70K", "CoinDesk", 2) + item("Is a btc wallet worth it?", "Blog", 2) + "</channel></rss>"
out, _ = news("Bitcoin", "btc", feed=coins)
check("the symbol counts only in capitals", [i["title"] for i in out["list"]] == ["BTC slips below $70K"])

meta = "<rss><channel>" + item("Meta sells $1B of bonds", "Reuters", 2) + item("Platforms are changing", "Wired", 2) + "</channel></rss>"
out, _ = news("Meta Platforms", "META", feed=meta)
check("a company's first word names it in a headline", [i["title"] for i in out["list"]] == ["Meta sells $1B of bonds"])

out, _ = news("Nvidia", "NVDA", refuse=True)
check("with Google News unreadable there is no section", out == {"list": None, "markdown": None})
out, _ = news("Nvidia", "NVDA", feed="<rss><channel></channel></rss>")
check("with nothing to show there is no section", out["list"] == [] and out["markdown"] is None)

old = "<rss><channel>" + item("Nvidia, two days on", "Bloomberg", 48) + "</channel></rss>"
out, _ = news("Nvidia", "NVDA", feed=old)
check("an older headline's age reads in days", out["markdown"].endswith("· Bloomberg · 2d"))

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
