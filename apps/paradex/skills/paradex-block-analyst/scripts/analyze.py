#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
analyze.py — benchmark one resolved Option Monitor block and render it.

Reads block.json and history.json (collect.py wrote them), fetches every leg's
live ticker concurrently, and prints the finished block (--render): net
greeks, the fill against the mark, the week's same-structure history, and the
live quotes.

Each block is benchmarked on the venue it settled on:
  DBT   the leg's own Deribit trade, which carries the mark and IV at the
        trade, and the live Deribit ticker for greeks and quotes;
  PRDX  Paradex's live market summary;
  OKX   OKX's live mark, book and Black-Scholes greeks;
  DRV   Derive's live ticker;
  else  the same option's live Deribit ticker (Bullish), against the fill's
        USD price.

Exit 0: stdout is the answer. Exit 1: stdout ends with a "## For the agent"
section holding the task and every number already pulled.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

DERIBIT = "https://www.deribit.com/api/v2/public"
PARADEX = "https://api.prod.paradex.trade/v1"
OKX = "https://www.okx.com/api/v5"
DERIVE = "https://api.derive.xyz/v3"
TIMEOUT_S = 6
AGENT_MARK = "## For the agent"
VENUES = {"DBT": "Deribit", "PRDX": "Paradex", "DRV": "Derive", "BLSH": "Bullish", "OKX": "OKX"}
WARN: list[str] = []
# Instruments a venue answered 400 for: not listed there, so no retry fixes it.
UNLISTED: set[str] = set()


def warn(m):
    WARN.append(m)


def _read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _transient(exc) -> bool:
    """A failure a second attempt can fix: the network, a timeout, a 5xx or a
    429. A 4xx is an answer (no such instrument) and is not retried."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def _open_json(url, timeout=TIMEOUT_S):
    # Derive's edge refuses a request with no User-Agent.
    req = urllib.request.Request(url, headers={"User-Agent": "paradex-block-analyst"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 — re-raised unless one retry may fix it
            if attempt == 2 or not _transient(exc):
                raise
            time.sleep(0.3)


def _deribit(path, params):
    d = _open_json(f"{DERIBIT}/{path}?{urlencode(params)}")
    if "error" in d:
        raise RuntimeError(d["error"])
    return d["result"]


def _paradex(path, params=None):
    return _open_json(f"{PARADEX}/{path}" + (f"?{urlencode(params)}" if params else ""))


def _failed(sym, e):
    if isinstance(e, urllib.error.HTTPError) and e.code == 400:
        UNLISTED.add(sym)
    warn(f"ticker {sym}: {e}")
    return sym, None


def fetch_ticker(sym):
    try:
        t = _deribit("ticker", {"instrument_name": sym})
        g = t.get("greeks") or {}
        return sym, {"mark": t.get("mark_price"), "bid": t.get("best_bid_price"),
                     "ask": t.get("best_ask_price"), "iv": t.get("mark_iv"),
                     "delta": g.get("delta"), "vega": g.get("vega"),
                     "gamma": g.get("gamma"), "theta": g.get("theta"),
                     "under": t.get("underlying_price"), "index": t.get("index_price")}
    except Exception as e:  # noqa: BLE001
        return _failed(sym, e)


def fetch_paradex_ticker(sym):
    """fetch_ticker's fields from Paradex's markets/summary. `mark_iv` is a
    decimal there and a percentage on Deribit, so it is scaled here. A greek
    Paradex does not publish is None, never 0. Bid and ask fall back to bbo/."""
    try:
        page = _paradex("markets/summary", {"market": sym})
        row = (page.get("results") or [None])[0]
        if not row:
            raise RuntimeError("no market summary")
        g = row.get("greeks") or {}
        bid, ask = core._f(row.get("bid")), core._f(row.get("ask"))
        if bid is None or ask is None:
            try:
                bbo = _paradex(f"bbo/{quote(sym, safe='')}")
                # An empty book answers "0"/"0" with last_updated_at 0.
                if bbo.get("last_updated_at"):
                    bid = core._f(bbo.get("bid")) if bid is None else bid
                    ask = core._f(bbo.get("ask")) if ask is None else ask
            except Exception as e:  # noqa: BLE001
                warn(f"bbo {sym}: {e}")
        iv = core._f(row.get("mark_iv"))
        delta = g.get("delta") if g.get("delta") is not None else row.get("delta")
        return sym, {"mark": core._f(row.get("mark_price")), "bid": bid, "ask": ask,
                     "iv": None if iv is None else round(iv * 100, 2),
                     "delta": core._f(delta), "vega": core._f(g.get("vega")),
                     "gamma": core._f(g.get("gamma")), "theta": core._f(g.get("theta")),
                     "under": core._f(row.get("underlying_price")), "index": None}
    except Exception as e:  # noqa: BLE001
        return _failed(sym, e)


def _okx(path, params):
    d = _open_json(f"{OKX}/{path}?{urlencode(params)}")
    if str(d.get("code")) != "0":
        raise RuntimeError(d.get("msg") or d.get("code"))
    return d.get("data") or []


def fetch_okx_ticker(sym):
    """OKX's mark, book and greeks for one option, in coin like its prices.
    The *BS greeks are per coin and in USD for vega and theta, Deribit's
    units; markVol is a decimal."""
    try:
        uly, expiry = "-".join(sym.split("-")[:2]), sym.split("-")[2]
        mark = (_okx("public/mark-price", {"instType": "OPTION", "instId": sym}) or [{}])[0]
        book = (_okx("market/ticker", {"instId": sym}) or [{}])[0]
        summ = next((x for x in _okx("public/opt-summary", {"uly": uly, "expTime": expiry})
                     if x.get("instId") == sym), None)
        if not mark.get("markPx"):
            raise RuntimeError("no mark")
        g = summ or {}
        iv = core._f(g.get("markVol"))
        return sym, {"mark": core._f(mark.get("markPx")), "bid": core._f(book.get("bidPx")),
                     "ask": core._f(book.get("askPx")),
                     "iv": None if iv is None else round(iv * 100, 2),
                     "delta": core._f(g.get("deltaBS")), "vega": core._f(g.get("vegaBS")),
                     "gamma": core._f(g.get("gammaBS")), "theta": core._f(g.get("thetaBS")),
                     "under": core._f(g.get("fwdPx")), "index": None}
    except Exception as e:  # noqa: BLE001
        return _failed(sym, e)


def fetch_derive_ticker(sym):
    """Derive's v3 ticker, in its compact field names: M mark, b/a best bid
    and ask, I index, option_pricing d/g/v/t greeks (vega and theta in USD)
    and i the IV as a decimal."""
    try:
        d = _open_json(f"{DERIVE}/public/get_ticker?{urlencode({'instrument_name': sym})}")
        if d.get("error"):
            UNLISTED.add(sym)
            raise RuntimeError(d["error"])
        t = d.get("result") or {}
        op = t.get("option_pricing") or {}
        iv = core._f(op.get("i"))
        return sym, {"mark": core._f(t.get("M")), "bid": core._f(t.get("b")),
                     "ask": core._f(t.get("a")),
                     "iv": None if iv is None else round(iv * 100, 2),
                     "delta": core._f(op.get("d")), "vega": core._f(op.get("v")),
                     "gamma": core._f(op.get("g")), "theta": core._f(op.get("t")),
                     "under": core._f(op.get("f")), "index": core._f(t.get("I"))}
    except Exception as e:  # noqa: BLE001
        return _failed(sym, e)


def fetch_trade_mark(sym, trade_id, at_ms):
    """The mark and IV Deribit recorded on this leg's own trade, or None."""
    try:
        r = _deribit("get_last_trades_by_instrument_and_time",
                     {"instrument_name": sym, "start_timestamp": at_ms - 2000,
                      "end_timestamp": at_ms + 2000, "count": 100})
        for t in r.get("trades") or []:
            if str(t.get("trade_id")) == trade_id:
                return trade_id, {"mark": core._f(t.get("mark_price")), "iv": core._f(t.get("iv"))}
        warn(f"trade {sym} {trade_id}: not in Deribit's trades at {at_ms}")
    except Exception as e:  # noqa: BLE001
        warn(f"trade {sym} {trade_id}: {e}")
    return trade_id, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/tmp/analyze")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args()
    try:
        return _run(args)
    except Exception as e:  # noqa: BLE001 — never a traceback; hand the block on
        try:
            block = _read_json(os.path.join(args.dir, "block.json"))
        except Exception:  # noqa: BLE001 — the fallback itself must not raise
            block = {}
        if not block:
            print("analyze: the analysis failed before the block was read.")
            return 0
        print(f"analyze: the analysis failed ({type(e).__name__}: {e}) after the block was found.")
        print(agent_section(
            "Build the analysis from the block below, as paradex-block-analyst describes: each "
            "leg's live ticker, the net greeks, and the fill against the mark.",
            {"block": block, "error": f"{type(e).__name__}: {e}"}, refetch=True))
        return 1


def agent_section(task: str, data: dict, refetch: bool = False) -> str:
    """The part of an exit-1 output only the agent reads: what to do, and every
    number the script already has."""
    rules = ("Fetch only what the task names; everything below was already pulled."
             if refetch else
             "Do not run analyze.sh again and do not fetch anything: everything you need is "
             "below. Reply with the block above, completed, and nothing before or after it.")
    return "\n".join(["", AGENT_MARK, "", task, "", rules, "", "```json",
                      json.dumps(data, default=str, indent=1), "```"])


def benchmark(venue):
    """'own' (Deribit, with the mark at the trade), 'paradex', 'okx',
    'derive', or 'deribit' (another venue's block against Deribit's live
    market)."""
    return {"DBT": "own", "PRDX": "paradex", "OKX": "okx", "DRV": "derive"}.get(venue, "deribit")


TICKERS = {"paradex": fetch_paradex_ticker, "okx": fetch_okx_ticker, "derive": fetch_derive_ticker}


def _run(args):
    block = _read_json(os.path.join(args.dir, "block.json"))
    hist = _read_json(os.path.join(args.dir, "history.json"))
    if not block:
        print("analyze: no block was resolved, so there is nothing to analyse.")
        return 0

    venue = (block.get("venue") or "").upper()
    asset = (block.get("underlying") or "").upper()
    legs, unit = core.legs_from_block(block)
    opt = core.option_legs(legs)
    how = benchmark(venue)

    for l in opt:
        if how == "own":
            l["_sym"] = l["instrument"]
        elif how == "paradex":
            l["_sym"] = l.get("paradex_market") or l["instrument"]
        elif how in ("okx", "derive"):
            l["_sym"] = l["instrument"]
        else:
            l["_sym"] = core.deribit_symbol(l["asset"], l["expiry_c"], l["strike"], l["cp"])
    ticker = TICKERS.get(how, fetch_ticker)
    perp = core.paradex_perp_symbol(asset) if how == "paradex" else core.perp_symbol(asset)
    perp_ticker = fetch_paradex_ticker if how == "paradex" else fetch_ticker
    syms = list(dict.fromkeys(l["_sym"] for l in opt))

    tickers, at_trade = {}, {}
    with ThreadPoolExecutor(max_workers=min(12, 2 * len(syms) + 2)) as ex:
        tf = [ex.submit(ticker, s) for s in syms] + [ex.submit(perp_ticker, perp)]
        mf = ([ex.submit(fetch_trade_mark, l["_sym"], l["leg_id"], l["traded_at"])
               for l in opt if l["leg_id"] and l["traded_at"]] if how == "own" else [])
        for f in tf:
            s, v = f.result()
            tickers[s] = v
        for f in mf:
            k, v = f.result()
            at_trade[k] = v

    spot = None
    tp = tickers.get(perp)
    if tp:
        spot = tp.get("mark") or tp.get("index")
    if spot is None:
        spot = core._f(block.get("index_price"))

    # The fill and the mark in one unit per leg: the venue's own price, or USD
    # for a block benchmarked on another venue's market.
    quote = "USD"
    marks_at_trade = how == "own"
    for l in opt:
        t = tickers.get(l["_sym"])
        l["tkr"] = t
        if how == "own":
            quote = l["asset"] if core.deribit_inverse(l["asset"]) else "USDC"
            l["fill"] = l["price"]
            m = at_trade.get(l["leg_id"])
            l["mark"], l["iv"] = (m or {}).get("mark"), (m or {}).get("iv")
            marks_at_trade = marks_at_trade and l["mark"] is not None
        elif how in ("paradex", "derive", "okx"):
            # OKX quotes its options in coin, Paradex and Derive in USD.
            if how == "okx":
                quote = l["asset"]
            l["fill"] = l["price"]
            l["mark"], l["iv"] = (t or {}).get("mark"), (t or {}).get("iv")
        else:
            l["fill"] = l["price_usd"]
            mark = (t or {}).get("mark")
            if mark is not None and core.deribit_inverse(l["asset"]):
                index = (t or {}).get("index") or (t or {}).get("under")
                mark = mark * index if index else None
            l["mark"], l["iv"] = mark, (t or {}).get("iv")

    # One kind of mark for the whole package: a leg Deribit's trades did not
    # answer for moves every leg to the live mark.
    if how == "own" and not marks_at_trade:
        for l in opt:
            l["mark"], l["iv"] = (l["tkr"] or {}).get("mark"), (l["tkr"] or {}).get("iv")

    greeks = {}
    for l in legs:
        if l["cp"] == "FUT":
            greeks[core.leg_key(l)] = core.FUT_GREEKS
        elif l.get("tkr"):
            greeks[core.leg_key(l)] = l["tkr"]
    ng = core.net_greeks(legs, greeks, unit or 0)
    fill_net = core.package_net(legs, "fill")
    mark_net = core.package_net(legs, "mark")
    gaps = [_leg_lbl(l, False) for l in opt if not l.get("tkr") and l["_sym"] not in UNLISTED]
    unlisted = [_leg_lbl(l, False) for l in opt if not l.get("tkr") and l["_sym"] in UNLISTED]
    if spot is None:
        gaps.append("spot")

    result = {
        "asset": asset, "venue": venue, "route": block.get("route") or "DIRECT",
        "block_id": str(block.get("block_id") or ""),
        "traded_at": core.to_ms(block.get("traded_at")), "how": how,
        "unit": unit, "struct": core.struct_name(legs), "spot": spot, "quote": quote,
        "fill_net": fill_net, "mark_net": mark_net, "marks_at_trade": marks_at_trade,
        "offset": core.package_offset(fill_net, mark_net, quote),
        "complete": block.get("complete", True) is not False,
        "legs": legs, "net_greeks": ng, "gaps": gaps, "unlisted": unlisted, "history": hist,
        "warnings": WARN,
    }
    if args.render:
        text, code = render(result)
        print(text)
        return code
    print(json.dumps(result, default=str))
    return 0


def _sk(strike):
    k = round(strike, 4)
    if float(k).is_integer():
        k = int(k)
        return f"{k // 1000}k" if k >= 1000 and k % 1000 == 0 else str(k)
    return f"{k:g}"


def _exp_short(ec):
    """'31JUL26' → '31Jul'."""
    if not ec:
        return ""
    i = len(ec) - 5
    return f"{ec[:i]}{ec[i:i + 3].title()}"


def _leg_lbl(l, multi_exp):
    base = f"{_sk(l['strike'])}{l['cp']}"
    return f"{base}·{_exp_short(l.get('expiry_c'))}" if multi_exp and l.get("expiry_c") else base


def _quote(t) -> str:
    bid = t.get("bid") or None
    ask = t.get("ask") or None
    if bid is None and ask is None:
        return "no quotes"
    return f"{'–' if bid is None else f'{bid:g}'}/{'–' if ask is None else f'{ask:g}'}"


def _num(x) -> str:
    return f"{x:,.6g}" if abs(x) >= 1 else f"{x:.6g}"


def _offset_txt(r) -> str:
    off = r["offset"]
    if off.get("txt", "n/a") == "n/a":
        return "n/a vs mark"
    s = off.get("sign", 0)
    where = "above" if s > 0 else "below" if s < 0 else "at"
    return f"{off['txt']} {where} {'mark at trade' if r['marks_at_trade'] else 'live mark'}"


def _signed(x) -> str:
    """USD greeks: whole dollars, except a small block's, which would print 0."""
    return f"{x:+,.0f}" if abs(x) >= 10 else f"{x:+.2f}"


def _greek_parts(ng, a) -> str:
    parts = [(f"Δ {ng['delta']:+.2f} {a}" if ng.get("delta") is not None else None),
             (f"Vega {_signed(ng['vega'])}/v" if ng.get("vega") is not None else None),
             (f"Γ {ng['gamma']:+.4f}" if ng.get("gamma") is not None else None),
             (f"Θ {_signed(ng['theta'])}/d" if ng.get("theta") is not None else None)]
    return " · ".join(p for p in parts if p)


def _history_text(h) -> str:
    if not h or h.get("unavailable"):
        return "Options tape history unavailable right now"
    n = h.get("count", 0)
    days = h.get("days", 7)
    more = "+" if h.get("truncated") else ""
    if not n:
        return f"No other block of this structure on the options tape, {days}d"
    split = " · ".join(f"{VENUES.get(v, v)} {c}" for v, c in
                       sorted((h.get("by_venue") or {}).items(), key=lambda x: -x[1]))
    return f"{n}{more} other same-structure block(s) on the options tape, {days}d ({split})"


def _when(ms) -> str:
    if not ms:
        return ""
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return d.strftime("%-d %b %H:%M UTC")


def _notes(r) -> list[str]:
    out = []
    if r["how"] == "deribit":
        out.append(f"{VENUES.get(r['venue'], r['venue'])}'s market is not read here, so the legs "
                   f"are benchmarked on Deribit's live market, in USD.")
    elif r["how"] != "own":
        out.append(f"The tape keeps no mark at the trade, so the fill is against "
                   f"{VENUES.get(r['venue'], r['venue'])}'s live mark.")
    elif not r["marks_at_trade"]:
        out.append("Deribit's mark at the trade could not be read for every leg, so the fill is "
                   "against the live mark.")
    if r.get("unlisted"):
        where = "Deribit" if r["how"] == "deribit" else VENUES.get(r["venue"], r["venue"])
        out.append(f"{where} lists no {', '.join(r['unlisted'])}, so those legs have no benchmark.")
    if not r["complete"]:
        out.append("The tape marks this block incomplete: a leg may still be missing.")
    if r["gaps"]:
        out.append(f"No live data for {', '.join(r['gaps'])} after a retry. "
                   f"Rerun /paradex_analyze to try again.")
    return [f"_{n}_" for n in out]


def render(r) -> tuple[str, int]:
    """(text, exit code). 0 when the text is the answer, 1 when it ends with a
    task for the agent."""
    a = r["asset"]
    legs = r["legs"]
    opt = sorted(core.option_legs(legs), key=lambda l: (l.get("expiry_ms") or 0, l["strike"], l["cp"]))
    venue = VENUES.get(r["venue"], r["venue"])
    route = "via Paradigm" if r["route"] == "PARADIGM" else "direct"
    sp = f"{r['spot']:,.0f}" if r.get("spot") else "unavailable"

    if not opt or r["unit"] is None:
        L = [f"**{a} block {r['venue']}:{r['block_id']}** · {venue} {route}", "",
             f"Spot {sp} · {_history_text(r['history'])}"]
        L.append(agent_section(
            "The block's option legs could not be read. Work them out from the block's legs "
            "below and finish the analysis: a header line `**<asset> <expiry> <strikes> "
            "<structure> · ×N | Buyer/Seller | Paid/Recd <price> | <offset>**`, a line "
            "`Spot … · <structure> · <venue>`, and a table with Greeks, Fair, History "
            "(as above) and Live.",
            {"legs": legs, "venue": r["venue"], "spot": r["spot"]}, refetch=True))
        return "\n".join(L), 1

    fill = r["fill_net"]
    verb = "Paid" if (fill or 0) >= 0 else "Recd"
    side = "Buyer" if (fill or 0) >= 0 else "Seller"
    price = f"{verb} {_num(abs(fill))}" if fill is not None else "price n/a"
    multi_exp = len({l["expiry_c"] for l in opt}) > 1
    exps = "/".join(dict.fromkeys(l["expiry_c"] for l in opt))
    strikes = "/".join(_sk(l["strike"]) for l in opt)
    L = [f"**{a} {exps} {strikes} {r['struct']} · ×{r['unit']:g} | {side} | {price} | "
         f"{_offset_txt(r)}**", ""]
    hedge = sum(l["sign"] * l["size"] for l in legs if l["cp"] == "FUT")
    hedge_txt = f" · hedge {hedge:+g} {a}" if hedge else ""
    L.append(f"Spot {sp} · {r['struct']}{hedge_txt} · {venue} {route} · {_when(r['traded_at'])}")
    L.append("")

    ng = r["net_greeks"]
    missing = [l for l in opt if not l.get("tkr")]
    rows = []
    if missing:
        rows.append(("Greeks", "net unavailable — no live data for "
                     + ", ".join(_leg_lbl(l, multi_exp) for l in missing)))
    else:
        rows.append(("Greeks", _greek_parts(ng, a)))
    ivs = " / ".join(f"{_leg_lbl(l, multi_exp)} " + (f"{l['iv']:g}v" if l.get("iv") is not None else "no data")
                     for l in opt)
    rows.append(("Fair", f"{_offset_txt(r)} · {ivs}"))
    rows.append(("History", _history_text(r["history"])))
    live = " · ".join(f"{_leg_lbl(l, multi_exp)} " + (_quote(l["tkr"]) if l.get("tkr") else "no data")
                      for l in opt)
    rows.append(("Live", live + (" (Deribit)" if r["how"] == "deribit" else "")))
    L += ["|  | Detail |", "| --- | --- |"]
    L += [f"| {label} | {text} |" for label, text in rows]
    notes = _notes(r)
    if notes:
        L += [""] + notes
    if r["warnings"]:
        L.append(f"<!-- warnings: {re.sub('-{2,}', '-', '; '.join(r['warnings']))} -->")
    return "\n".join(L), 0


if __name__ == "__main__":
    sys.exit(main())
