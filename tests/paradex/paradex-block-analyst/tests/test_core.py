#!/usr/bin/env python3
"""core.py against real blocks from the options tape. Run: python3 tests/test_core.py"""
from _harness import Tally, block
import core

t = Tally()

# references
t.ok(core.parse_ref("DBT:BLOCK-302780") == ("DBT", "BLOCK-302780"), "Deribit ref")
t.ok(core.parse_ref("DRV:01a1388e-cb52-7f72-bf85-983d5fb212e4")[0] == "DRV", "Derive uuid ref")
t.ok(core.parse_ref(" PRDX:1791942177030201709226870000 ")[1] == "1791942177030201709226870000",
     "Paradex numeric ref, padded")
for bad in ("", "BLOCK-302780", "dbt:1", "DBT:", "DBT:a b", "DBT:../x", "--help", "DBT:1;rm"):
    t.ok(core.parse_ref(bad) is None, f"refuses {bad!r}")

# expiries and symbols
t.ok(core.compact_expiry(1792137600000) == "16OCT26", "ms → Deribit expiry, no leading zero")
t.ok(core.compact_expiry("2026-10-09T08:00:00Z") == "9OCT26", "ISO expiry from a processed block")
t.ok(core.to_ms("2026-10-14T04:02:54.310Z") == 1791950574310, "ISO traded_at → ms")
t.ok(core.deribit_symbol("BTC", "16OCT26", 80000.0, "P") == "BTC-16OCT26-80000-P", "BTC symbol")
t.ok(core.deribit_symbol("XRP", "14OCT26", 1.2, "C") == "XRP_USDC-14OCT26-1d2-C", "decimal strike uses d")
t.ok(core.deribit_symbol("SOL", "14OCT26", 150, "C") == "SOL_USDC-14OCT26-150-C", "alt is USDC")

# legs: the taker's side signs each leg, the smallest option leg is the unit
legs, unit = core.legs_from_block(block("DBT:BLOCK-302780"))
t.ok(unit == 10 and len(legs) == 4, "iron condor: 4 legs ×10")
signs = {(l["cp"], l["strike"]): l["sign"] for l in legs}
t.ok(signs == {("P", 80000): 1, ("C", 84000): -1, ("P", 83500): -1, ("C", 87000): 1},
     f"condor signs from each leg's side: {signs}")
t.ok(core.struct_name(legs) == "Iron Condor", "named Iron Condor")
t.ok(all(l["ratio"] == 1 for l in legs), "equal sizes are ratio 1")

legs, unit = core.legs_from_block(block("PRDX:1791942177030201709226870000"))
t.ok(core.struct_name(legs) == "Put Spread" and unit == 73, "Paradex put spread ×73")

legs, _ = core.legs_from_block({"underlying": "BTC", "legs": [
    {"kind": "OPTION", "option_type": "CALL", "strike": "90000", "expiry": 1792137600000,
     "side": "BUY", "size": "2", "price": "0.01", "leg_id": "1", "instrument": "a"},
    {"kind": "OPTION", "option_type": "CALL", "strike": "95000", "expiry": 1792137600000,
     "side": "SELL", "size": "4", "price": "0.005", "leg_id": "2", "instrument": "b"},
    {"kind": "OPTION", "option_type": "CALL", "strike": "100000", "expiry": 1792137600000,
     "side": "BUY", "size": "2", "price": "0.002", "leg_id": "3", "instrument": "c"},
    {"kind": "PERP", "option_type": None, "strike": None, "expiry": None,
     "side": "SELL", "size": "0.5", "price": "84000", "leg_id": "4", "instrument": "BTC-PERPETUAL"}]})
t.ok(core.struct_name(legs) == "Call Butterfly + perp", "1:2:1 calls with a perp")
t.ok([l["ratio"] for l in legs] == [1, 2, 1, 0.25], "ratios over the smallest option leg")
t.ok(core.package_net(legs, "price") == 0.01 - 0.01 + 0.002, "package net leaves the hedge out")

def four(spec):
    return core.legs_from_block({"underlying": "BTC", "legs": [
        {"kind": "OPTION", "option_type": "CALL" if cp == "C" else "PUT", "strike": str(k),
         "expiry": 1792137600000, "side": side, "size": str(n), "instrument": f"{k}{cp}"}
        for side, cp, k, n in spec]})[0]


t.ok(core.struct_name(four([("BUY", "P", 80, 1), ("SELL", "P", 90, 1), ("SELL", "C", 100, 1), ("BUY", "C", 110, 1)]))
     == "Iron Condor", "long wings, short body, puts below calls")
t.ok(core.struct_name(four([("BUY", "C", 80, 1), ("SELL", "C", 90, 1), ("SELL", "P", 100, 1), ("BUY", "P", 110, 1)]))
     == "Custom", "calls below puts is not an iron condor")
t.ok(core.struct_name(four([("BUY", "P", 80, 1), ("SELL", "P", 90, 2), ("SELL", "C", 100, 1), ("BUY", "C", 110, 1)]))
     == "Custom", "uneven sizes are not a condor")
t.ok(core.struct_name(four([("BUY", "C", 80, 1), ("SELL", "C", 90, 1), ("SELL", "C", 100, 1), ("BUY", "C", 110, 1)]))
     == "Call Condor", "four calls")

t.ok(core.legs_from_block({"legs": [{"kind": "PERP", "side": "BUY", "size": "1"}]})[1] is None,
     "a hedge-only block has no unit")

# history key ignores size, side and venue
a, b = block("DBT:BLOCK-302780"), dict(block("DBT:BLOCK-302780"))
b["legs"] = [dict(l, side="SELL" if l["side"] == "BUY" else "BUY", size="99") for l in a["legs"]]
b["venue"] = "DRV"
t.ok(core.structure_key(a) == core.structure_key(b), "same structure whatever the size, side, venue")
c = dict(a, legs=[dict(a["legs"][0], strike="79000")] + a["legs"][1:])
t.ok(core.structure_key(a) != core.structure_key(c), "a different strike is another structure")

# net greeks: all or nothing, and a missing greek is unknown
legs, unit = core.legs_from_block(block("PRDX:1791942177030201709226870000"))
g = {core.leg_key(l): {"delta": -0.3 if l["strike"] == 2300 else -0.1, "vega": 1.0,
                       "gamma": 0.001, "theta": None} for l in legs}
ng = core.net_greeks(legs, g, unit)
sold_2300 = next(l for l in legs if l["strike"] == 2300)["sign"] == -1
t.ok(sold_2300 and abs(ng["delta"] - 73 * (0.3 - 0.1)) < 1e-9, f"short 2300P long 2200P is long delta: {ng}")
t.ok(ng["theta"] is None, "a greek the venue does not publish stays unknown")
t.ok(core.net_greeks(legs, {}, unit) == {}, "no greeks when a leg has none")

# offsets
t.ok(core.offset(0.0131, 0.0135, "BTC")["txt"] == "-4 bps", "coin offset in bps")
t.ok(core.offset(2.90, 2.7494, "USD")["txt"] == "+5.5%", "USD offset in percent")
t.ok(core.package_offset(-0.0153, -0.01556, "BTC")["sign"] == -1, "a smaller credit is below mark")
t.ok(core.package_offset(0.001, -0.002, "BTC")["sign"] == 1, "a debit where the mark said credit is above")

t.done()
