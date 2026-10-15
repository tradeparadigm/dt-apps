#!/usr/bin/env python3
"""
core.py — pure, dependency-free logic for the Paradex block analyst.

No network, no auth, no deps. Given one options-tape block (the Option
Monitor's row) and live greeks, it works out the legs, the structure's name,
the net greeks and the fill against the mark. analyze.py fetches; this module
never does.

Every leg on the tape carries the taker's side, so signs are never inferred
here: the Paradigm analyst's description parsing has nothing to do.
"""
from __future__ import annotations

import datetime as dt
import re

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# The block's (venue, block_id): venue codes are upper-case letters, block ids
# are whatever the venue hands out (BLOCK-302780, a uuid, a long number).
_REF = re.compile(r"^([A-Z]{2,10}):([A-Za-z0-9._-]{1,128})$")
HEDGE_KINDS = ("PERP", "FUTURE")


def parse_ref(raw: str):
    """'DBT:BLOCK-302780' → ('DBT', 'BLOCK-302780'), or None."""
    m = _REF.match((raw or "").strip())
    return (m.group(1), m.group(2)) if m else None


def _f(v):
    try:
        if v in (None, "", "NULL"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def to_ms(v):
    """A tape time as unix ms. The tape sends ms; a host that processed the
    block first may send an ISO string instead."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    try:
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def compact_expiry(ms) -> str | None:
    """1792137600000 → '16OCT26', Deribit's form (no leading zero)."""
    ms = to_ms(ms)
    if ms is None:
        return None
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return f"{d.day}{MONTHS[d.month - 1]}{d.year % 100:02d}"


def deribit_symbol(asset: str, expiry_c: str, strike, cp: str) -> str:
    """BTC/ETH → 'BTC-31JUL26-66000-C'; USDC-margined alts → 'SOL_USDC-…'.
    Deribit writes a decimal strike with a d: XRP_USDC-14OCT26-1d2-C."""
    base = asset.upper()
    if base not in ("BTC", "ETH"):
        base = f"{base}_USDC"
    k = f"{round(float(strike), 6):g}".replace(".", "d")
    return f"{base}-{expiry_c}-{k}-{cp.upper()}"


def deribit_inverse(asset: str) -> bool:
    """Deribit quotes BTC and ETH options in coin; every other asset in USDC."""
    return asset.upper() in ("BTC", "ETH")


def perp_symbol(asset: str) -> str:
    base = asset.upper()
    return f"{base}-PERPETUAL" if base in ("BTC", "ETH") else f"{base}_USDC-PERPETUAL"


def paradex_perp_symbol(asset: str) -> str:
    return f"{asset.upper()}-USD-PERP"


# ── legs ──────────────────────────────────────────────────────────────────────

def legs_from_block(block: dict) -> tuple[list[dict], float | None]:
    """(legs, unit). One unit of the package is its smallest OPTION leg, the
    rule the Option Monitor and the tape's own `quantity` follow, so each leg's
    ratio is its size over that. A hedge leg's size is already in coin, so its
    ratio is exact. unit is None when the block has no usable option leg."""
    raw = block.get("legs") or []
    sizes = [_f(l.get("size")) for l in raw if l.get("kind") == "OPTION"]
    sizes = [s for s in sizes if s and s > 0]
    unit = min(sizes) if sizes else None
    legs = []
    for l in raw:
        size = _f(l.get("size"))
        side = (l.get("side") or "").upper()
        if not size or size <= 0 or side not in ("BUY", "SELL") or unit is None:
            continue
        sign = 1 if side == "BUY" else -1
        base = {
            "asset": (l.get("underlying") or block.get("underlying") or "").upper(),
            "instrument": l.get("instrument") or "",
            "leg_id": str(l.get("leg_id") or ""),
            "sign": sign, "ratio": size / unit, "size": size,
            "price": _f(l.get("price")), "price_usd": _f(l.get("price_usd")),
            "index": _f(l.get("index_price")), "traded_at": to_ms(l.get("traded_at")),
            "paradex_market": l.get("paradex_market"),
        }
        if l.get("kind") == "OPTION":
            cp = "C" if (l.get("option_type") or "").upper() == "CALL" else "P"
            strike = _f(l.get("strike"))
            expiry_c = compact_expiry(l.get("expiry"))
            if strike is None or expiry_c is None:
                continue
            legs.append({**base, "cp": cp, "strike": strike, "expiry_c": expiry_c,
                         "expiry_ms": to_ms(l.get("expiry"))})
        elif l.get("kind") in HEDGE_KINDS:
            legs.append({**base, "cp": "FUT", "strike": 0.0, "expiry_c": compact_expiry(l.get("expiry"))})
    return legs, unit


def option_legs(legs):
    return [l for l in legs if l["cp"] != "FUT"]


def structure_key(block: dict) -> tuple | None:
    """What makes two blocks the same structure: the same coin, and the same
    option legs (type, strike, expiry), whatever the size, side or venue."""
    keys = []
    for l in block.get("legs") or []:
        if l.get("kind") != "OPTION":
            continue
        strike, expiry = _f(l.get("strike")), compact_expiry(l.get("expiry"))
        if strike is None or expiry is None:
            return None
        keys.append(((l.get("underlying") or block.get("underlying") or "").upper(),
                     (l.get("option_type") or "").upper(), strike, expiry))
    return tuple(sorted(keys)) if keys else None


def struct_name(legs: list[dict]) -> str:
    """The structure's name from its legs, in the vocabulary the Paradigm
    analyst uses. Signs are the taker's, so a Buyer and a Seller of one
    structure get the same name."""
    opt = option_legs(legs)
    perp = " + perp" if any(l["cp"] == "FUT" for l in legs) else ""
    if not opt:
        return "Hedge" + perp
    cps = {l["cp"] for l in opt}
    expiries = {l["expiry_c"] for l in opt}
    strikes = sorted({l["strike"] for l in opt})
    one = "Call" if cps == {"C"} else "Put" if cps == {"P"} else None
    base = "Custom"
    if len(opt) == 1:
        base = one
    elif len(opt) == 2:
        a, b = sorted(opt, key=lambda l: l["strike"])
        opposite = a["sign"] * b["sign"] < 0
        same_k = a["strike"] == b["strike"]
        if cps == {"C", "P"} and len(expiries) == 1:
            base = ("Combo" if opposite else "Straddle") if same_k else (
                "Risk Reversal" if opposite else "Strangle")
        elif one and len(expiries) > 1 and opposite:
            base = f"{one} {'Calendar' if same_k else 'Diagonal'}"
        elif one and not same_k and opposite:
            base = f"{one} Spread"
    elif len(expiries) == 1 and len(opt) == 3 and one and len(strikes) == 3:
        lo, mid, hi = sorted(opt, key=lambda l: l["strike"])
        if lo["sign"] == hi["sign"] == -mid["sign"] and mid["ratio"] == 2 * lo["ratio"] == 2 * hi["ratio"]:
            base = f"{one} Butterfly"
    elif len(expiries) == 1 and len(opt) == 4 and len(strikes) in (3, 4) \
            and len({l["ratio"] for l in opt}) == 1:
        lo_to_hi = sorted(opt, key=lambda l: (l["strike"], l["cp"] == "C"))
        wings = lo_to_hi[0]["sign"] == lo_to_hi[-1]["sign"]
        body = all(l["sign"] == -lo_to_hi[0]["sign"] for l in lo_to_hi[1:3])
        # An iron structure is puts below calls: the two lowest legs are puts.
        iron_ok = one or [l["cp"] for l in lo_to_hi] == ["P", "P", "C", "C"]
        if wings and body and iron_ok:
            kind = "Butterfly" if len(strikes) == 3 else "Condor"
            base = f"{one} {kind}" if one else f"Iron {kind}"
    return base + perp


# ── greeks and the fill against the mark ──────────────────────────────────────

def net_greeks(legs: list[dict], greek_by_key: dict, qty: float) -> dict:
    """net_g = Σ (sign × ratio × per-leg greek) × qty, per greek.
    Returns {} unless EVERY leg has greeks — a partial sum is a silently wrong
    net. A greek one leg's source does not publish is None for the package."""
    if not legs or any(not greek_by_key.get(leg_key(l)) for l in legs):
        return {}
    out = {}
    for k in ("delta", "vega", "gamma", "theta"):
        if any(greek_by_key[leg_key(l)].get(k) is None for l in legs):
            out[k] = None
            continue
        out[k] = qty * sum(l["sign"] * l["ratio"] * float(greek_by_key[leg_key(l)][k]) for l in legs)
    return out


FUT_GREEKS = {"delta": 1.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}


def leg_key(l: dict) -> str:
    if l["cp"] == "FUT":
        return f"FUT:{l['instrument']}"
    return f"{l['cp']}:{l['strike']:g}:{l['expiry_c']}:{l['asset']}"


def package_net(legs: list[dict], field: str) -> float | None:
    """Σ sign × ratio × leg[field] over the option legs: what one package cost
    (positive) or paid (negative). None when any option leg lacks the field."""
    opt = option_legs(legs)
    if not opt or any(l.get(field) is None for l in opt):
        return None
    return sum(l["sign"] * l["ratio"] * l[field] for l in opt)


_STABLE_QUOTES = {"USD", "USDC", "USDT"}


def package_offset(fill_net, ref_net, quote: str = "") -> dict:
    """Fill against mark for a package, positive when the fill was richer.

    |net_fill| against |net_mark| is the display convention, and it only holds
    while both are the same kind. A debit filled where the mark said credit
    costs the taker both, so the signed pair is compared there instead.
    """
    if fill_net is None or ref_net is None or not ref_net:
        return {"txt": "n/a", "sign": 0}
    if (fill_net > 0) != (ref_net > 0):
        base = abs(ref_net)
        return offset(base + (fill_net - ref_net), base, quote)
    return offset(abs(fill_net), abs(ref_net), quote)


def offset(price, ref, quote: str = "") -> dict:
    """bps for a coin quote with a fraction premium (BTC 0.0131), percent for
    a dollar quote or a dollar-sized premium."""
    if price is None or ref is None:
        return {"txt": "n/a", "sign": 0}
    d = price - ref
    sign = 1 if d > 0 else -1 if d < 0 else 0
    if (quote or "").upper() not in _STABLE_QUOTES and abs(ref) < 1:
        bps = round(d * 10000, 1)
        return {"txt": f"{bps:+g} bps", "sign": sign, "val": bps, "unit": "bps"}
    pct = round(d / ref * 100, 1) if ref else None
    return {"txt": (f"{pct:+g}%" if pct is not None else "n/a"),
            "sign": sign, "val": pct, "unit": "%"}
