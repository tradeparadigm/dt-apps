#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
analyze.py — single-call orchestrator for the block analyst.

ONE invocation does everything after the tape resolve: it reads the FILL/HIST
CSVs collect_analysis.py wrote (from analyze.sh), parses the structure, fetches every
leg's Deribit ticker + 30d trade buckets CONCURRENTLY, computes net greeks /
direction / fill-offset / recurrence, and prints the finished block (--render).

Exit 0: stdout is the answer, for the user as it stands — including what could
not be fetched, said in the block. Exit 1: stdout needs an agent; it ends with a
"## For the agent" section holding the task and every number already pulled, so
the agent finishes the block without re-running or re-fetching anything.

Deterministic + no per-turn tool orchestration ⇒ fast and run-to-run stable.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_core as ac  # noqa: E402

DERIBIT = "https://www.deribit.com/api/v2/public"
# Public market data, no credential. A PRDX block is benchmarked on the venue it
# settled on: Deribit has no listing for most of what trades there, and where it
# does, its mark is a different book's price set beside a Paradex fill.
PARADEX = "https://api.prod.paradex.trade/v1"
WARN: list[str] = []
# Short enough that a request, its one retry and Paradex's bbo/ fallback all fit
# inside analyze.sh's deadline for this step.
TIMEOUT_S = 6
AGENT_MARK = "## For the agent"


def warn(m):
    WARN.append(m)


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


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


def _open_json(url, timeout):
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 — re-raised unless one retry may fix it
            if attempt == 2 or not _transient(exc):
                raise
            time.sleep(0.3)


def _get(path, params, timeout=TIMEOUT_S):
    d = _open_json(f"{DERIBIT}/{path}?{urlencode(params)}", timeout)
    if "error" in d:
        raise RuntimeError(d["error"])
    return d["result"]


def fetch_ticker(sym):
    try:
        t = _get("ticker", {"instrument_name": sym})
        g = t.get("greeks") or {}
        return sym, {"mark": t.get("mark_price"), "bid": t.get("best_bid_price"),
                     "ask": t.get("best_ask_price"), "iv": t.get("mark_iv"),
                     "delta": g.get("delta"), "vega": g.get("vega"),
                     "gamma": g.get("gamma"), "theta": g.get("theta"),
                     "oi": t.get("open_interest"), "under": t.get("underlying_price"),
                     "index": t.get("index_price")}
    except Exception as e:  # noqa: BLE001
        warn(f"ticker {sym}: {e}")
        return sym, None


def _paradex(path, params=None, timeout=TIMEOUT_S):
    return _open_json(f"{PARADEX}/{path}" + (f"?{urlencode(params)}" if params else ""), timeout)


def fetch_paradex_ticker(sym):
    """The same fields fetch_ticker returns, from Paradex's markets/summary.

    `mark_iv` is a DECIMAL on Paradex (0.52 is 52%) and a percentage on Deribit,
    so it is scaled here and the render prints one unit. Greeks sit in a nested
    `greeks` object; one Paradex does not publish is None, never 0, so the net
    leaves it out rather than summing a missing value as nothing. Bid and ask
    come from the summary when it carries them and from bbo/ when it does not.
    """
    try:
        page = _paradex("markets/summary", {"market": sym})
        row = (page.get("results") or [None])[0]
        if not row:
            raise RuntimeError("no market summary")
        g = row.get("greeks") or {}
        bid, ask = ac._f(row.get("bid")), ac._f(row.get("ask"))
        if bid is None or ask is None:
            try:
                bbo = _paradex(f"bbo/{sym}")
                # An empty book answers "0"/"0" with last_updated_at 0 — no
                # quote, not a zero price — and the summary leaves both blank.
                if bbo.get("last_updated_at"):
                    bid = ac._f(bbo.get("bid")) if bid is None else bid
                    ask = ac._f(bbo.get("ask")) if ask is None else ask
            except Exception as e:  # noqa: BLE001
                warn(f"bbo {sym}: {e}")
        iv = ac._f(row.get("mark_iv"))
        delta = g.get("delta") if g.get("delta") is not None else row.get("delta")
        return sym, {"mark": ac._f(row.get("mark_price")), "bid": bid, "ask": ask,
                     "iv": None if iv is None else round(iv * 100, 2),
                     "delta": ac._f(delta), "vega": ac._f(g.get("vega")),
                     "gamma": ac._f(g.get("gamma")), "theta": ac._f(g.get("theta")),
                     "oi": ac._f(row.get("open_interest")),
                     "under": ac._f(row.get("underlying_price")), "index": None}
    except Exception as e:  # noqa: BLE001
        warn(f"ticker {sym}: {e}")
        return sym, None


def fetch_trades_bucket(sym, now_ms):
    """30d prints/blocks/contracts by 24h/7d/30d for one instrument.
    The whole body is guarded: one malformed trade record must degrade THIS
    instrument to None (warned), never leak an exception into the caller."""
    start = now_ms - 30 * 86400_000
    try:
        r = _get("get_last_trades_by_instrument",
                 {"instrument_name": sym, "count": 1000, "start_timestamp": start,
                  "end_timestamp": now_ms, "sorting": "desc"})
        t = r.get("trades") or []
        if not t:
            return sym, {"24h": (0, 0, 0.0), "7d": (0, 0, 0.0), "30d": (0, 0, 0.0)}
        latest = max(x.get("timestamp") or 0 for x in t)

        def bucket(days):
            c = latest - days * 86400_000
            w = [x for x in t if (x.get("timestamp") or 0) >= c]
            b = [x for x in w if x.get("block_trade_id")]
            return len(w), len(b), round(sum(float(x.get("amount") or 0) for x in w), 1)
        return sym, {"24h": bucket(1), "7d": bucket(7), "30d": bucket(30)}
    except Exception as e:  # noqa: BLE001
        warn(f"trades {sym}: {e}")
        return sym, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", default="/tmp/analyze")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args()
    try:
        return _run(args)
    except Exception as e:  # noqa: BLE001 — never a traceback; hand the resolved rows on
        try:
            rows = _read_csv(os.path.join(args.csv_dir, "fill.csv"))
        except Exception:  # noqa: BLE001 — the fallback itself must not raise
            rows = []
        if not rows:
            print("analyze: the analysis failed before the trade was read.")
            return 0
        print(f"analyze: the analysis failed ({type(e).__name__}: {e}) after the trade was found.")
        print(agent_section(
            "Build the block from the trade rows below, as paradigm-block-analyst Steps 1–7 "
            "describe: legs from the rows (never the user's text), each leg's live ticker on the "
            "trade's venue, the net greeks, and the fill against the mark.",
            {"fill_rows": rows, "error": f"{type(e).__name__}: {e}"},
            refetch=True))
        return 1


def agent_section(task: str, data: dict, refetch: bool = False) -> str:
    """The part of an exit-1 output only the agent reads: what to do, and every
    number the script already has, so the turn is scoped to the one gap."""
    rules = ("Fetch only what the task names; everything below was already pulled."
             if refetch else
             "Do not run analyze.sh again and do not fetch anything: everything you need is "
             "below. Reply with the block above, completed, and nothing before or after it.")
    return "\n".join(["", AGENT_MARK, "", task, "", rules, "", "```json",
                      json.dumps(data, default=str, indent=1), "```"])


def _run(args):
    import time
    now_ms = args.now_ms or int(time.time() * 1000)

    fill = _read_csv(os.path.join(args.csv_dir, "fill.csv"))
    hist = _read_csv(os.path.join(args.csv_dir, "hist.csv"))
    if not fill:
        print("analyze: no trade was resolved, so there is nothing to analyse.")
        return 0

    prod = ac.parse_product(fill[0].get("PRODUCT", ""))
    asset = prod["asset"]
    desc = fill[0].get("DESCRIPTION", "")
    quote = (fill[0].get("QUOTE_CURRENCY") or "").upper()
    qty = ac.structure_unit(fill)
    qty_inferred = not ac.package_size_certain(fill)
    # Two tape shapes: (a) one combined-DESCRIPTION block (ICondor/Cstm/single) →
    # parse fill[0]; (b) one row PER LEG, each a single-leg desc or a perp/future →
    # build legs from the rows, sign straight from each row's SIDE (most reliable).
    unmapped = False
    legs = ac.legs_from_rows(fill)
    if legs is not None:
        side = "Buyer" if ac.net_cash(fill) > 0 else "Seller"
        # each row's SIDE is its leg's sign; a perp leg's SIZE is a separate
        # question (hedge status, below).
        reliable = True
        parsed = {"code": "combo"}
    else:
        parsed = ac.parse_description(desc)
        if parsed["classified"] and parsed["legs"]:
            legs, side, reliable = ac.apply_orientation(parsed, fill)
        else:
            # Structure name not mapped. Safe fallback ladder (correctness > speed):
            # 1) if the description still lists explicit legs (Type/date/strike), pull
            #    them → we can fetch correct per-leg data; model assigns the net.
            # 2) otherwise legs stay empty → emit the raw tape rows + strikes and let
            #    the model build the whole block. Never a confident empty/guessed block.
            legs = ac.extract_legs_generic(desc)
            side = "Buyer" if ac.net_cash(fill) > 0 else "Seller"
            # explicit per-leg signs are authoritative even under an unmapped name;
            # whether the description listed EVERY leg is what stays unknown.
            reliable = bool(legs) and all(l.get("sign") is not None for l in legs)
            unmapped = True
        # The rows name each leg's instrument even when the DESCRIPTION repeats
        # the package, so a name this parser cannot sign (an unmapped one, or a
        # risk reversal, whose roles the name does not fix) still has exact legs:
        # instrument for strike and type, each row's own SIDE for the sign.
        if unmapped or not reliable:
            by_instrument = ac.legs_from_instruments(fill)
            if by_instrument is not None:
                legs = by_instrument
                side = "Buyer" if ac.net_cash(fill) > 0 else "Seller"
                reliable = True
                if unmapped:
                    parsed = {"code": "combo"}
                unmapped = False

    # A perp/future hedge nets only when every hedge row became a leg whose size
    # two sources confirm. Otherwise the options net alone, and the block says so:
    # the size is a fact about the trade nobody downstream can supply.
    hedge_rows = [r for r in fill
                  if ac.parse_product(r.get("PRODUCT", "")).get("kind") in ("PERPETUAL", "FUTURE")]
    fut_legs = [l for l in legs if l["cp"] == "FUT"]
    if not hedge_rows:
        hedge = "none"
    elif len(fut_legs) == len(hedge_rows) and ac.hedges_sized(legs):
        hedge = "sized"
    else:
        hedge = "unconfirmed"
    net_legs = [l for l in legs if l["cp"] != "FUT" or hedge == "sized"]

    # instruments: each option leg + the perp for spot, on the venue the block
    # settled on. Paradex carries no 30d block-trade buckets in this script, so
    # those are Deribit's alone.
    on_paradex = prod["venue"] == "PRDX"
    symbol = ac.paradex_symbol if on_paradex else ac.deribit_symbol
    ticker = fetch_paradex_ticker if on_paradex else fetch_ticker
    syms = []
    for l in legs:
        if l["cp"] != "FUT" and l.get("expiry_c"):
            l["_sym"] = symbol(asset, l["expiry_c"], l["strike"], l["cp"])
            syms.append(l["_sym"])
    perp = ac.paradex_perp_symbol(asset) if on_paradex else ac.perp_symbol(asset)

    # fetch everything concurrently: tickers (legs+perp) + per-leg 30d trades.
    # Submit all up front so they run in parallel, then collect into typed maps.
    tickers, buckets = {}, {}
    with ThreadPoolExecutor(max_workers=min(12, 2 * len(syms) + 2)) as ex:
        tfuts = [ex.submit(ticker, s) for s in syms + [perp]]
        bfuts = [] if on_paradex else [ex.submit(fetch_trades_bucket, s, now_ms) for s in syms]
        for f in tfuts:
            s, v = f.result()
            tickers[s] = v
        for f in bfuts:
            s, v = f.result()
            buckets[s] = v

    spot = None
    tp = tickers.get(perp)
    if tp:
        spot = tp.get("mark") or tp.get("index")
    if spot is None:
        for l in legs:
            tt = tickers.get(l.get("_sym"))
            if tt and tt.get("under"):
                spot = tt["under"]
                break

    # greeks per leg key
    greek_by_key = {}
    for l in legs:
        tt = tickers.get(l.get("_sym"))
        if tt:
            greek_by_key[ac.leg_key(l)] = tt
        elif l["cp"] == "FUT":
            # delta 1 per coin, nothing else: a sized hedge's ratio is coins per
            # package unit, so net_greeks counts it in coin like the option legs.
            greek_by_key[ac.leg_key(l)] = ac.FUT_GREEKS
    ng = ac.net_greeks(net_legs, greek_by_key, qty) if reliable else {}
    # A greek one leg's venue does not publish is unknown for the package, not 0.
    for k in list(ng):
        if any(greek_by_key[ac.leg_key(l)].get(k) is None for l in net_legs):
            ng[k] = None
    # What did not come back after its retry, said in the block rather than
    # handed to anyone: a retry is the only fix, and the user can rerun.
    gaps = [_leg_lbl(l, False) for l in legs
            if l["cp"] != "FUT" and l.get("_sym") and tickers.get(l["_sym"]) is None]
    if spot is None:
        gaps.append("spot")
    counts_missing = (not on_paradex
                      and any(buckets.get(l.get("_sym")) is None for l in legs if l.get("_sym")))

    # Net package offset (SKILL Step 7, the ONE convention) — per structure unit, unit by quote.
    # struct_net weights each option leg by its QTY relative to the structure's base unit, so a
    # 1×2×1 fly's body counts twice (net = +wing − 2×body + wing); a plain per-row sum over-states
    # it. package_offset compares |net_fill| vs |net_mark| — the displayed Paid/Recd magnitude — so
    # a positive result always means the fill was richer than mark (above), negative cheaper
    # (below), deterministically, regardless of debit/credit. It compares the signed pair instead
    # when the two straddle zero, where magnitudes say nothing. Never a per-leg OFFSET_BPS.
    # Single-leg reduces to (PRICE − REF_PRICE) × 10000 (backward compatible).
    fill_net = ac.struct_net(fill, "PRICE")
    ref_net = ac.struct_net(fill, "REF_PRICE")
    # A fill from Paradigm's API or the terminal carries the package mark, not a
    # mark per leg, so collect_analysis.py hands it over already netted.
    package = _read_json(os.path.join(args.csv_dir, "package.json"))
    if package.get("ref_net") is not None:
        ref_net = float(package["ref_net"])
    if not fill_net and package.get("fill_net") is not None:
        fill_net = float(package["fill_net"])
    off = ac.package_offset(fill_net, ref_net, quote)

    # recurrence: HIST blocks clustered by BLOCK_TRADE_ID
    blocks = {}
    for r in hist:
        b = r.get("BLOCK_TRADE_ID")
        if b:
            blocks.setdefault(b, []).append(r)
    recurrence = len(blocks)

    history = _read_json(os.path.join(args.csv_dir, "history.json"))

    # grfq (multi-maker) vs drfq (directed) — from the resolved RFQ_ID's routing
    # prefix (GRFQ- / DRFQv2-), the authoritative source per SKILL Step 0.
    rfq_kind = "grfq" if (fill[0].get("RFQ_ID") or "").upper().startswith("GRFQ") else "drfq"

    result = {
        "asset": asset, "venue": prod["venue"], "structure": parsed["code"],
        "rfq_kind": rfq_kind,
        "desc": desc, "side": side, "qty": qty, "qty_inferred": qty_inferred,
        "reliable_signs": reliable,
        "unmapped": unmapped, "spot": spot, "quote": quote,
        "fill_net": round(fill_net, 6), "ref_net": round(ref_net, 6), "offset": off,
        "legs": [{"cp": l["cp"], "strike": l["strike"], "ratio": l["ratio"],
                  "sign": l["sign"], "sized": l.get("sized", False), "expiry": l.get("expiry_c"), "sym": l.get("_sym"),
                  "tkr": tickers.get(l.get("_sym")), "trades": buckets.get(l.get("_sym"))}
                 for l in legs],
        # raw tape rows — the authoritative ground truth for the model to build from
        # in the unmapped/⚠ cases (always correct straight from the resolved block).
        "fill_rows": [{"desc": r.get("DESCRIPTION"), "side": r.get("SIDE"),
                       "qty": ac._f(r.get("QTY")), "price": ac._f(r.get("PRICE")),
                       "ref": ac._f(r.get("REF_PRICE")), "product": r.get("PRODUCT")}
                      for r in fill],
        "net_greeks": ng, "recurrence_blocks": recurrence, "warnings": WARN,
        "hedge": hedge,
        "hedge_rows": [{"instrument": r.get("INSTRUMENT") or r.get("PRODUCT"),
                        "side": r.get("SIDE"), "qty": ac._f(r.get("QTY")),
                        "price": ac._f(r.get("PRICE")), "ratio": ac._f(r.get("RATIO"))}
                       for r in hedge_rows],
        "gaps": gaps, "counts_missing": counts_missing,
        "history_unavailable": bool(history.get("unavailable")),
        "history_as_of": history.get("as_of") or "",
    }

    if args.render:
        text, code = render(result)
        print(text)
        return code
    print(json.dumps(result, default=str))
    return 0


def _sk(strike):
    k = int(round(strike))
    return f"{k//1000}k" if k >= 1000 and k % 1000 == 0 else str(k)


def _exp_short(ec):
    """Compact expiry '31JUL26' → '31Jul' (drop year) for terse leg tags."""
    m = re.match(r"(\d+)([A-Za-z]{3})", ec or "")
    return f"{m.group(1)}{m.group(2).title()}" if m else (ec or "")


def _leg_lbl(l, multi_exp):
    """Per-leg label '60500C'; append '·3Jul' only when the structure spans >1 expiry
    (calendars/diagonals) so same-strike legs are distinguishable — else stays clean."""
    base = f"{_sk(l['strike'])}{l['cp']}"
    return f"{base}·{_exp_short(l.get('expiry'))}" if multi_exp and l.get("expiry") else base


def _struct_name(code, legs):
    """Human structure name in the DRFQ StrategyCodeEnum vocabulary (rfq-trader
    references/instruments.md): Butterfly family (never "Fly"), typed calendars.
    Condor/butterfly reflect leg composition (a 4-call block is a Call Condor, not
    an Iron Condor); a cross-expiry pair with different strikes is a Diagonal.
    Perp/future leg noted."""
    opt = [l for l in legs if l["cp"] != "FUT"]
    perp = " + perp" if any(l["cp"] == "FUT" for l in legs) else ""
    allc = bool(opt) and all(l["cp"] == "C" for l in opt)
    allp = bool(opt) and all(l["cp"] == "P" for l in opt)
    if code == "CO":
        base = "Call Condor" if allc else "Put Condor" if allp else "Iron Condor"
    elif code == "BF":
        base = "Call Butterfly" if allc else "Put Butterfly" if allp else "Iron Butterfly"
    elif code == "CA":
        kind = "Calendar" if len({l["strike"] for l in opt}) <= 1 else "Diagonal"
        base = f"Call {kind}" if allc else f"Put {kind}" if allp else kind
    elif code == "combo":
        # per-leg-rows package: signs are exact (row SIDE), so name 2-leg shapes
        # with the same vocabulary paradigm-options-recap uses (same-strike C&P traded
        # opposite ways = synthetic forward = "Combo").
        base = "Combo"
        if len(opt) == 2:
            a, b = sorted(opt, key=lambda l: l["strike"])
            cps = {a["cp"], b["cp"]}
            same_k = a["strike"] == b["strike"]
            same_e = a.get("expiry_c") == b.get("expiry_c")
            opp = (a["sign"] or 0) * (b["sign"] or 0) < 0
            if cps == {"C", "P"} and same_e:
                base = (("Combo" if opp else "Straddle") if same_k
                        else ("Risk Reversal" if opp else "Strangle"))
            elif len(cps) == 1 and not same_e:
                kind = "Calendar" if same_k else "Diagonal"
                base = f"{'Call' if cps == {'C'} else 'Put'} {kind}"
            elif len(cps) == 1 and not same_k:
                base = f"{'Call' if cps == {'C'} else 'Put'} Spread"
    else:
        base = {"CL": "Call", "PL": "Put", "ST": "Straddle", "SN": "Strangle",
                "CS": "Call Spread", "PS": "Put Spread",
                "RR": "Risk Reversal", "CM": "Custom"}.get(code, code)
    return base + perp


def _offset_txt(off) -> str:
    """'-6 bps below mark' — the net package offset rendered with its direction word.
    Direction from the sign of (|net_fill| − |net_mark|): above = fill richer than mark,
    below = cheaper, at = equal. Neutral token — above/below is a fact, never an edge/against
    judgement (SKILL Step 7 forbids moralizing)."""
    t = off.get("txt", "n/a")
    if t == "n/a":
        return "n/a vs mark"
    s = off.get("sign", 0)
    return f"{t} {'above' if s > 0 else 'below' if s < 0 else 'at'} mark"


def _quote(t) -> str:
    """'7.58/9.88', 'no quotes' for an empty book, '–' for a missing side.
    Both venues report an empty side as 0 or nothing; neither is a price."""
    bid = t.get("bid") or None
    ask = t.get("ask") or None
    if bid is None and ask is None:
        return "no quotes"
    return f"{bid if bid is not None else '–'}/{ask if ask is not None else '–'}"


def _history_text(r) -> str:
    if r.get("history_unavailable"):
        return "Paradigm block history unavailable right now"
    text = f"{r['recurrence_blocks']} same-structure block(s) on Paradigm 30d"
    if r.get("history_as_of"):
        text += f" (as of {r['history_as_of']})"
    return text


def _greek_parts(ng, a) -> str:
    parts = [(f"Δ {ng['delta']:+.2f} {a}" if ng.get("delta") is not None else None),
             (f"Vega {ng['vega']:+,.0f}/v" if ng.get("vega") is not None else None),
             (f"Γ {ng['gamma']:+.4f}" if ng.get("gamma") is not None else None),
             (f"Θ {ng['theta']:+,.0f}/d" if ng.get("theta") is not None else None)]
    return " · ".join(p for p in parts if p)


def _agent_data(r) -> dict:
    """Every number the script pulled, for an agent finishing the block."""
    return {
        "asset": r["asset"], "venue": r["venue"], "side": r["side"], "N": r["qty"],
        "fill_net": r["fill_net"], "ref_net": r["ref_net"], "spot": r["spot"],
        "legs": [{k: l.get(k) for k in ("cp", "strike", "expiry", "ratio", "sign", "sym")}
                 | {"greeks": {k: (l.get("tkr") or {}).get(k)
                               for k in ("delta", "gamma", "vega", "theta", "iv", "mark")}}
                 for l in r["legs"]],
        "fill_rows": r["fill_rows"],
    }


def _amt(x: float) -> str:
    return f"{int(x):,}" if float(x).is_integer() else f"{x:,.6g}"


def _notes(r, legs, a) -> list[str]:
    """One line each for what the block could not settle, said to the user."""
    out = []
    if r.get("qty_inferred"):
        sizes = "/".join(f"{row['qty']:g}" for row in r["fill_rows"]
                         if row.get("qty") and "OPTION" in (row.get("product") or ""))
        out.append(f"Leg sizes {sizes} state no ratio, so ×{r['qty']:g} takes the smallest leg "
                   f"as one package. If the package is larger, ×N, {r['verb']} and a bps offset "
                   f"change with it; the greeks are for the whole block either way.")
    if r.get("hedge") == "unconfirmed":
        rows = "; ".join(f"{h['side']} {_amt(h['qty'])} {h['instrument']} @ {_amt(h['price'])}"
                         for h in r.get("hedge_rows") or [] if h.get("qty") and h.get("price"))
        out.append(f"Greeks are the options alone: the perp hedge's size is not confirmed by "
                   f"the trade data ({rows}).")
    if r.get("gaps") or r.get("counts_missing"):
        what = list(r.get("gaps") or [])
        if r.get("counts_missing"):
            what.append("Deribit's 30-day leg counts")
        out.append(f"No live data for {', '.join(what)} after a retry. "
                   f"Rerun /analyze to try again.")
    if r["venue"] not in ("DBT", "PRDX"):
        out.append("Benchmarked on Deribit.")
    return [f"_{n}_" for n in out]


def render(r) -> tuple[str, int]:
    """(text, exit code). 0 when the text is the answer; 1 when it ends with a
    task for the agent — only for what the trade data cannot settle by rule."""
    a = r["asset"]
    legs = r["legs"]
    verb = "Paid" if r["fill_net"] >= 0 else "Recd"
    r["verb"] = verb
    fillabs = abs(r["fill_net"])
    sp = f"{r['spot']:,.0f}" if r.get("spot") else "unavailable"

    # The structure could not be read and no legs came out of the description:
    # the resolved rows are right, the legs are what an agent has to work out.
    if not legs:
        L = [f"**{a} · {r['desc'].strip()} · ×{r['qty']:g} | {r['side']} | "
             f"{verb} {fillabs:g} | {_offset_txt(r['offset'])}** · {r['rfq_kind']}/{r['venue']}",
             "", f"Spot {sp} · {_history_text(r)}"]
        L.append(agent_section(
            "The structure could not be read from the trade data. Work out its legs from the "
            "rows' DESCRIPTION (never the user's text), fetch each leg's ticker on "
            f"{'Paradex' if r['venue'] == 'PRDX' else 'Deribit'}, and finish the block: "
            "a header line `**<asset> <expiry> <strikes> <structure> · ×N | Buyer/Seller | "
            "Paid/Recd <price> | <offset>**`, a line `Spot … · <structure> · drfq/<venue>`, "
            "and a table with Greeks (net: Σ sign × ratio × leg greek × N), Fair (offset and "
            "leg IVs), History (as above) and Live (leg bid/ask). Side, price and offset "
            "above are final.",
            {**_agent_data(r), "history": _history_text(r)}, refetch=True))
        return "\n".join(L), 1

    multi_exp = len({l.get("expiry") for l in legs if l["cp"] != "FUT" and l.get("expiry")}) > 1
    exp = legs[0]["expiry"] if legs else "?"
    strikes = "/".join(_sk(l["strike"]) for l in legs if l["cp"] != "FUT")
    struct = _struct_name(r["structure"], legs)
    L = [f"**{a} {exp} {strikes} {struct} · ×{r['qty']:g} | {r['side']} | "
         f"{verb} {fillabs:g} | {_offset_txt(r['offset'])}**", ""]
    hedge = sum(l["sign"] * l["ratio"] * r["qty"] for l in legs if l["cp"] == "FUT" and l.get("sized"))
    hedge_txt = f" · hedge {hedge:+.2f} {a} perp" if hedge else ""
    note = "signs verified" if r["reliable_signs"] and not r.get("unmapped") else "draft"
    L.append(f"Spot {sp} · {struct}{hedge_txt} · {note} · {r['rfq_kind']}/{r['venue']}")
    L.append("")

    opt = [l for l in legs if l["cp"] != "FUT"]
    missing = [l for l in opt if not l.get("tkr")]
    rows = []
    ng = r["net_greeks"]
    if not r["reliable_signs"]:
        per = " · ".join(f"{_leg_lbl(l, multi_exp)} Δ{(l['tkr'] or {}).get('delta')}"
                         for l in opt if l.get("tkr"))
        rows.append(("Greeks", f"per leg (signs to confirm): {per}"))
    elif missing:
        rows.append(("Greeks", "net unavailable — no live data for "
                     + ", ".join(_leg_lbl(l, multi_exp) for l in missing)))
    else:
        rows.append(("Greeks", _greek_parts(ng, a)))
    ivs = " / ".join(f"{_leg_lbl(l, multi_exp)} "
                     + (f"{l['tkr'].get('iv')}v" if l.get("tkr") else "no data") for l in opt)
    rows.append(("Fair", f"{_offset_txt(r['offset'])} · {ivs}"))
    history = _history_text(r)
    if r["venue"] != "PRDX":
        if r.get("counts_missing"):
            history += " · Deribit leg blocks 30d: –"
        else:
            d30 = sum((l["trades"] or {}).get("30d", (0, 0, 0))[1] for l in legs if l.get("trades"))
            history += f" · Deribit leg blocks 30d: {d30}"
    rows.append(("History", history))
    live = " · ".join(f"{_leg_lbl(l, multi_exp)} "
                      + (_quote(l["tkr"]) if l.get("tkr") else "no data") for l in opt)
    rows.append(("Live", live))
    L += ["|  | Detail |", "| --- | --- |"]
    L += [f"| {label} | {text} |" for label, text in rows]
    notes = _notes(r, legs, a)
    if notes:
        L += [""] + notes
    if r["warnings"]:
        L.append(f"<!-- warnings: {'; '.join(r['warnings'])} -->")

    # What only judgment can settle goes to the agent, with the numbers.
    if r.get("unmapped"):
        L.append(agent_section(
            "The structure's name was not recognised and the legs above were read from its "
            "description text, which can miss a leg. Check them against fill_rows. If they "
            "match, reply with the block as it stands, changing `draft` to `signs verified`. "
            "If a leg is missing or wrong, correct the legs and redo the Greeks, Fair and Live "
            "rows — fetching only a leg that is not in the data.",
            _agent_data(r), refetch=True))
        return "\n".join(L), 1
    if not r["reliable_signs"]:
        L.append(agent_section(
            "Which legs the taker is long and which short could not be read from the trade "
            "data. Settle each option leg's sign from fill_rows (a row naming one leg carries "
            "the taker's SIDE for it) and the structure, then replace the Greeks row with the "
            f"net: Σ sign × ratio × leg greek × N, N = {r['qty']:g}, and `draft` with "
            "`signs verified`. Buyer/Seller, Paid/Recd and the offset in the header are final.",
            _agent_data(r)))
        return "\n".join(L), 1
    return "\n".join(L), 0


if __name__ == "__main__":
    sys.exit(main())
