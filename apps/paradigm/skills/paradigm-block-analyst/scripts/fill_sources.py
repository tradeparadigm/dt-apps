"""Where a fill comes from when the execution tape does not have it yet.

The tape trails the market by the upstream sync (hourly), so a block analysed
minutes after it printed is not on it. Two other sources carry the same trade:

  injected  the trade JSON the terminal attaches to the /analyze message — the
            rows the UI's Unified RFQs view shows, read from Paradigm's API
  api       GET /v2/drfq/trade_tape/ on api.prod.paradigm.trade, the same
            endpoint, authenticated with the account's enrolled production key

Both arrive in the API's shape and are converted here into the rows
collect_analysis.py writes from the tape, so analyze.py renders them through the
same path. Stdlib only: collect_analysis.py's own deps are the reader's.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

API_HOST = "api.prod.paradigm.trade"
API_PATH = "/v2/drfq/trade_tape/"
# The names DIME's credential form derives for a Paradigm key enrolled against
# the mainnet environment (venueCredentialLabel: <venue>-<env>-<slug>). The
# proxy swaps the placeholders for the real values and signs; the script never
# holds either secret. Production only: every other source this skill reads is
# production data, so a testnet key would search a tape it cannot analyse.
ACCESS_VAR = "CRED_PARADIGM_MAINNET_ACCESS"
SIGN_VAR = "CRED_PARADIGM_MAINNET_SIGNING"
SIGN_HEADER = "X-Dime-Sign-paradigm-mainnet-signing"
PAGE_SIZE = 200
MAX_PAGES = 5
FILLED_STATES = {"FILLED", "COMPLETED"}

_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
_OPTION = re.compile(r"^(?P<asset>[^-]+)-(?P<d>\d{1,2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})-"
                     r"(?P<k>[\d.]+)-(?P<cp>[CP])$")


def core_id(value: str) -> str:
    return (value or "").removeprefix("DRFQv2-").removeprefix("GRFQ-")


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num(value: float) -> str:
    return f"{value:.10g}"


def _leg_kind(name: str) -> str:
    if _OPTION.match(name or ""):
        return "OPTION"
    return "PERPETUAL" if "PERP" in (name or "").upper() else "FUTURE"


def _asset(trade: dict) -> str:
    for leg in trade.get("legs") or []:
        name = leg.get("instrument_name") or ""
        if name:
            return name.split("-")[0].split("_")[0].upper()
    return ""


def rows_from_trade(trade: dict) -> tuple[list[dict], dict]:
    """One API trade → the per-leg rows collect_analysis.shaped() produces.

    The API states the structure's orientation on the legs and the taker's
    direction on the package (Paradigm's own RFQ skill: legs define the
    structure, the cross side carries the direction). The tape's per-leg
    taker_side is the leg as the taker holds it, so each row's SIDE is the two
    combined. The trade's own prices prove the combination: the legs net to the
    package price only this way, and `check` records whether they do.

    The API carries a package mark, not a per-leg one. Single-leg rows get it as
    REF_PRICE; multi-leg rows leave REF_PRICE blank and the package mark rides
    in the returned package dict, which analyze.py uses for the offset.
    """
    top = 1 if (trade.get("side") or "BUY").upper() == "BUY" else -1
    venue = (trade.get("venue") or "").upper()
    asset = _asset(trade)
    legs = trade.get("legs") or []
    rid = trade.get("rfq_id") or ""
    bid = trade.get("id") or ""
    rows, net = [], 0.0
    qtys = [q for q in (_f(l.get("quantity")) for l in legs) if q]
    base = min(qtys) if qtys else 1.0
    for i, leg in enumerate(legs):
        leg_sign = 1 if (leg.get("side") or "BUY").upper() == "BUY" else -1
        sign = top * leg_sign
        kind = _leg_kind(leg.get("instrument_name") or "")
        price = _f(leg.get("price"))
        qty = _f(leg.get("quantity"))
        if kind == "OPTION" and price is not None:
            net += sign * ((qty or base) / base) * price
        rows.append({
            "PRODUCT": f"{asset} {kind} - {venue}",
            "DESCRIPTION": trade.get("description") or "",
            "QTY": leg.get("quantity"), "PRICE": leg.get("price"),
            "REF_PRICE": trade.get("mark_price") if len(legs) == 1 else "",
            "SIDE": "BUY" if sign > 0 else "SELL",
            "QUOTE_CURRENCY": (trade.get("quote_currency") or "").upper(),
            "RFQ_ID": rid, "TRADE_ID": f"{bid}:{i}", "BLOCK_TRADE_ID": bid,
            # The leg's own instrument and Paradigm's stated ratio: analyze.py
            # signs each leg from these when the package name cannot be parsed,
            # and sizes a perp hedge only where its QTY and ratio agree.
            "INSTRUMENT": leg.get("instrument_name") or "",
            "RATIO": leg.get("ratio") if leg.get("ratio") is not None else "",
            "_DESC_N": (trade.get("description") or "").upper().replace(" ", ""),
        })
    price, mark = _f(trade.get("price")), _f(trade.get("mark_price"))
    package = {
        "fill_net": None if price is None else top * price,
        "ref_net": None if mark is None else top * mark,
        "index_price": _f(trade.get("index_price")),
        "executed_at": _f(trade.get("executed_at")),
        "check": (price is None or not legs
                  or abs(net - top * price) <= 1e-6 * max(1.0, abs(price))),
    }
    return rows, package


def _legacy_rows(rows: list[dict]) -> list[dict]:
    """Rows already in the tape's uppercase CSV shape (what evals inject)."""
    out = []
    for r in rows:
        r = dict(r)
        r["_DESC_N"] = (r.get("DESCRIPTION") or "").upper().replace(" ", "")
        out.append(r)
    return out


def _candidates(obj):
    if isinstance(obj, list):
        for item in obj:
            yield from _candidates(item)
    elif isinstance(obj, dict):
        if "results" in obj and isinstance(obj["results"], list):
            yield from _candidates(obj["results"])
        elif "trade" in obj and isinstance(obj["trade"], dict):
            yield obj["trade"]
        else:
            yield obj


def from_injected(obj, core: str) -> tuple[list[dict], dict | None] | None:
    """Find the trade for `core` in whatever the terminal attached.

    Accepts one trade, a list of them, an API page ({"results": [...]}), or the
    tape's uppercase rows. Returns None when nothing in it is this RFQ — an
    injected payload is the visible tape, and the trade may not be on screen.
    """
    legacy = []
    for item in _candidates(obj):
        if "legs" in item and core_id(str(item.get("rfq_id") or "")) == core:
            if (item.get("state") or "FILLED").upper() not in FILLED_STATES:
                continue
            return rows_from_trade(item)
        if "RFQ_ID" in item and core_id(str(item.get("RFQ_ID") or "")) == core:
            legacy.append(item)
    if legacy:
        return _legacy_rows(legacy), None
    return None


def load_injected(path: str):
    with open(path, encoding="utf-8") as handle:
        text = handle.read().strip()
    # The terminal sends the context as `DATA: {...}` after the command; accept
    # the payload with or without that label.
    text = re.sub(r"^\s*DATA:\s*", "", text)
    return json.loads(text) if text else None


def _fetch(url: str, headers: dict, timeout: float = 10.0) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def lookup_api(core: str, *, stop_before_ms: float | None, now_ms: float,
               env=None, fetch=_fetch) -> tuple[dict | None, str]:
    """Page Paradigm's trade tape newest-first for `core`.

    Stops at the execution tape's coverage edge: anything older was in the read
    collect_analysis.py already searched. Returns (trade, "") or (None, reason),
    where reason is a sentence for the not-found line.
    """
    env = os.environ if env is None else env
    access, sign = env.get(ACCESS_VAR, ""), env.get(SIGN_VAR, "")
    if not access.startswith("cred-") or not sign.startswith("sign-"):
        return None, (f"no production Paradigm key enrolled ({ACCESS_VAR} and {SIGN_VAR} "
                      "not both set), so Paradigm's API was not searched")
    horizon = now_ms - 30 * 86400_000
    floor = max(horizon, stop_before_ms or horizon)
    cursor, seen = None, 0
    for _ in range(MAX_PAGES):
        query = {"page_size": PAGE_SIZE}
        if cursor:
            query["cursor"] = cursor
        target = f"{API_PATH}?{urlencode(query)}"
        ts = str(int(time.time() * 1000))
        headers = {
            "Authorization": f"Bearer {access}",
            "Paradigm-API-Timestamp": ts,
            "Paradigm-API-Signature": sign,
            SIGN_HEADER: base64.b64encode(f"{ts}\nGET\n{target}\n".encode()).decode(),
        }
        try:
            status, body = fetch(f"https://{API_HOST}{target}", headers)
        except Exception as exc:  # noqa: BLE001 — a network failure is a reason, not a crash
            return None, f"Paradigm's API was unreachable ({type(exc).__name__}: {exc})"
        if status != 200:
            return None, f"Paradigm's API refused the lookup (HTTP {status}: {body.strip()[:120]})"
        try:
            page = json.loads(body)
        except ValueError:
            return None, "Paradigm's API answered with something that is not JSON"
        results = page.get("results") or []
        seen += len(results)
        for trade in results:
            if core_id(str(trade.get("rfq_id") or "")) != core:
                continue
            state = (trade.get("state") or "FILLED").upper()
            if state not in FILLED_STATES:
                return None, f"Paradigm's API has it in state {state}, not filled"
            return trade, ""
        stamps = [t for t in (_f(r.get("executed_at")) for r in results) if t is not None]
        if not results or (stamps and min(stamps) < floor):
            return None, f"not among the {seen} newest trades on Paradigm's API either"
        cursor = page.get("next")
        if not cursor:
            return None, f"not among the {seen} trades on Paradigm's API either"
    return None, f"not among the {seen} newest trades on Paradigm's API (stopped after {MAX_PAGES} pages)"
