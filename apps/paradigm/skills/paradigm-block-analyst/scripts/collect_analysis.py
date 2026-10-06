#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35", "polars>=1.0", "pyarrow>=17"]
# ///
"""Resolve one RFQ to the two CSVs analyze.py renders from.

Replaces the DuckDB scan of hot__paradigm_trade_tape_30d that analyze.sh used
to build inline, along with its STS bootstrap: the shared reader in
paradigm-data-discovery resolves credentials through the chain, reads the exact daily
partitions, and refuses a stale or duplicate-ID publication instead of
returning rows from it.

ONE unfiltered 30-day read, filtered twice in memory:
  fill  — the target RFQ's legs, matched on the exact namespace set
  hist  — every OTHER block sharing the fill's PRODUCT and normalised
          DESCRIPTION, which is what recurrence counts. Filtering the read by
          rfq_id instead would leave hist holding only the fill's own block and
          recurrence would read 1 for every trade.
"""
import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "paradigm-data-discovery" / "scripts"))
from execution_tape import AmbiguousRfqError, read_executions  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fill_sources  # noqa: E402

HORIZON = dt.timedelta(days=30)
FILL_COLUMNS = ("PRODUCT", "DESCRIPTION", "QTY", "PRICE", "REF_PRICE", "SIDE",
                "QUOTE_CURRENCY", "RFQ_ID", "TRADE_ID", "BLOCK_TRADE_ID",
                "INSTRUMENT", "RATIO")
HIST_COLUMNS = ("PRODUCT", "DESCRIPTION", "QTY", "PRICE", "REF_PRICE", "SIDE",
                "BLOCK_TRADE_ID")


def core_id(value: str) -> str:
    return value.removeprefix("DRFQv2-").removeprefix("GRFQ-")


def quote_currency(row: dict) -> str:
    """DERIVED, not renamed — the tape has no premium-currency column.

    `asset` is the UNDERLYING. Aliasing it onto QUOTE_CURRENCY fed the wrong
    value to analyze_core.offset, whose `quote not in _STABLE_QUOTES and
    abs(ref) < 1` branch then reports a coin-priced fill as a USD one.
    """
    product = (row.get("product") or "")
    venue = product.split(" - ")[1].strip().upper() if " - " in product else ""
    asset = (row.get("asset") or "").upper()
    name = row.get("instrument_name")
    if (venue == "DBT" and asset in ("BTC", "ETH")
            and name is not None and "USDC" not in name.upper()):
        return asset
    return "USDC"


def normalised(description: str) -> str:
    return (description or "").upper().replace(" ", "")


def shaped(rows: list[dict]) -> list[dict]:
    """Tape rows in the column names analyze.py reads.

    row_type is still a live classification column and the reader applies no
    filter of its own, so keep the predicate the SQL had: any other row carries
    an rfq_id too, and would land in both fill and hist and inflate recurrence.

    `WHERE row_type='paradigm_trade'` in SQL, which drops a NULL — three-valued
    logic makes `NULL = 'x'` unknown, not true. Admitting NULL here inflated
    recurrence in exactly the way the sentence above says this prevents. A
    column absent from EVERY row is a different failure and is raised, not
    silently treated as a match.
    """
    # The VALUE, not the key: polars materialises every column, so a NULL or
    # renamed row_type keeps the key and this guard would pass while `shaped`
    # returned nothing — every id on the tape reporting as never traded.
    if rows and not any(row.get("row_type") == "paradigm_trade" for row in rows):
        raise KeyError(
            "no execution tape row carries row_type='paradigm_trade' — the column is "
            "renamed, NULL or gone; refusing to report every id as never traded")
    out = []
    for row in rows:
        if row.get("row_type") != "paradigm_trade":
            continue
        out.append({
            "PRODUCT": row.get("product"), "DESCRIPTION": row.get("description"),
            "QTY": row.get("quantity"), "PRICE": row.get("trade_price"),
            "REF_PRICE": row.get("mark_price"), "SIDE": row.get("taker_side"),
            "QUOTE_CURRENCY": quote_currency(row), "RFQ_ID": row.get("rfq_id"),
            "TRADE_ID": row.get("trade_id"), "BLOCK_TRADE_ID": row.get("block_trade_id"),
            "INSTRUMENT": row.get("instrument_name"),
            "_DESC_N": normalised(row.get("description")),
        })
    return out


def write(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# One upstream sync interval. `coverage_complete` is exact — it wants the
# watermark at `end_ms` to the millisecond — so with an hourly sync it is false
# on every live run. Using it to choose between "not found" and "not found yet"
# collapsed the two into the second: a mistyped id was answered with "retry
# after the next sync", advice that is never right and never stops being given.
def coverage_edge(result: dict) -> str:
    """How far the read actually reaches, as a sentence — or "" when it reaches
    the requested end.

    Deliberately NOT a verdict. A watermark cannot tell a trade that has not
    synced yet from one that never happened (execution_tape.py:74-77), so a
    threshold on it only moves the wrong answer: exact completeness said "retry
    after the next sync" to a typo, and a 90-minute grace said "not found" to a
    block traded 20 minutes ago. Naming the boundary answers both without the
    code guessing which case it is in.
    """
    if result.get("coverage_complete"):
        return ""
    watermark = result.get("source_watermark_ms")
    if watermark is None:
        # One partition predating the field is enough (execution_tape.py:169).
        return "the read's coverage is unknown — a partition predates the watermark"
    reached = dt.datetime.fromtimestamp(int(watermark) / 1000, dt.timezone.utc)
    return f"the read covers through {reached:%Y-%m-%d %H:%M}Z only"


def _structure(row: dict) -> tuple[str, str, str]:
    """Asset, venue and normalised description: the structure, whatever source.

    The tape's PRODUCT string is matched exactly when the fill came off the tape,
    as it always was. A fill from the API builds PRODUCT itself, and an exact
    match would then hang recurrence on two writers spelling a kind the same way.
    """
    product = row.get("PRODUCT") or ""
    left, _, venue = product.partition(" - ")
    asset = (left.split() or [""])[0].upper()
    return asset, venue.strip().upper(), row.get("_DESC_N") or ""


def history_as_of(result: dict, now: dt.datetime) -> str:
    """The tape's coverage edge as a reader would say it: '01:59 UTC', with the
    date only when it is not today's. "" when the read reaches the end."""
    if result.get("coverage_complete"):
        return ""
    watermark = result.get("source_watermark_ms")
    if watermark is None:
        return "an unknown time"
    reached = dt.datetime.fromtimestamp(int(watermark) / 1000, dt.timezone.utc)
    if reached.date() == now.astimezone(dt.timezone.utc).date():
        return f"{reached:%H:%M} UTC"
    return f"{reached:%-d %b %H:%M} UTC"


def collect(rfq_id: str, out_dir: Path, *, now=None, s3=None, injected=None,
            lookup=None) -> dict:
    """Resolve the fill: the injected trade, then the tape, then Paradigm's API.

    The tape is read either way, because the 30-day recurrence comes from it. A
    tape that cannot be read stops the run only when nothing else had the fill;
    otherwise the block renders and says the history is missing.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    lookup = lookup or fill_sources.lookup_api

    # An explicit namespace is honoured, matching execution_tape.read_executions.
    # Stripping it unconditionally made the ambiguity message's own remediation
    # ("re-run with the exact prefixed id") reproduce the ambiguity, leaving a
    # block that exists in both namespaces permanently unanalysable.
    core = core_id(rfq_id)
    if not core:
        raise ValueError("empty rfq_id")
    explicit = rfq_id.startswith(("DRFQv2-", "GRFQ-"))
    wanted = {rfq_id} if explicit else {core, f"DRFQv2-{core}", f"GRFQ-{core}"}

    fill, package, source, notes = None, None, None, []
    if injected is not None:
        found = fill_sources.from_injected(injected, core)
        if found:
            fill, package = found
            source = "injected"

    tape_error, result, rows = None, {}, []
    try:
        result = read_executions(now - HORIZON, now, s3=s3, now=now)
        rows = shaped(result["rows"])
    except Exception as exc:  # noqa: BLE001 — decided below, once the other sources are known
        tape_error = exc

    if fill is None and tape_error is None:
        tape_fill = [r for r in rows if r["RFQ_ID"] in wanted]
        if tape_fill:
            namespaces = {r["RFQ_ID"] for r in tape_fill}
            if len(namespaces) > 1:
                raise AmbiguousRfqError(
                    f"{core} exists in {len(namespaces)} namespaces ({', '.join(sorted(namespaces))}) — "
                    "re-run with the exact DRFQv2- or GRFQ- prefixed id")
            fill, source = tape_fill, "tape"

    api_reason = ""
    if fill is None:
        if explicit and rfq_id.startswith("GRFQ-"):
            # The API's ids carry no namespace and this is its DRFQ path, so a
            # match could not be told apart from a DRFQ block with the same core.
            api_reason = "Paradigm's API was not searched: its DRFQ tape cannot confirm a GRFQ- id"
        else:
            trade, api_reason = lookup(
                core, stop_before_ms=result.get("coverage_end_ms"),
                now_ms=now.timestamp() * 1000)
            if trade is not None:
                fill, package = fill_sources.rows_from_trade(trade)
                source = "api"

    coverage = {"coverage_complete": bool(result.get("coverage_complete")),
                "coverage_edge": "" if tape_error else coverage_edge(result),
                "history_as_of": "" if tape_error else history_as_of(result, now),
                "coverage_note": result.get("coverage_note")}
    if fill is None:
        if tape_error is not None:
            raise RuntimeError(f"{tape_error}; {api_reason}" if api_reason else str(tape_error))
        # The reader reports the hourly-sync tail as incomplete rather than
        # raising. Saying "not on the tape" for a trade inside that tail is the
        # substitution its contract forbids — absence of evidence read as
        # evidence of absence.
        return {"fill": 0, "hist": 0, "blocks": 0, "api_reason": api_reason, **coverage}

    if source == "tape":
        # Recurrence is about OTHER blocks of the same structure, so match on the
        # structure, not on the RFQ.
        structures = {(r["PRODUCT"], r["_DESC_N"]) for r in fill}
        hist = [r for r in rows if (r["PRODUCT"], r["_DESC_N"]) in structures]
    else:
        # The fill's own block is counted exactly once, as the tape path counts
        # it: tape rows for this RFQ are dropped and the fill's rows stand in, so
        # two sources spelling one block id differently cannot count it twice.
        structures = {_structure(r) for r in fill}
        hist = [r for r in rows if _structure(r) in structures
                and core_id(r["RFQ_ID"] or "") != core] + fill
        if package is not None and not package.get("check", True):
            notes.append("the legs' prices do not net to the package price on this trade, "
                         "so check Paid/Recd against the trade itself")

    out_dir.mkdir(parents=True, exist_ok=True)
    write(out_dir / "fill.csv", FILL_COLUMNS, fill)
    write(out_dir / "hist.csv", HIST_COLUMNS, hist)
    if package is not None:
        (out_dir / "package.json").write_text(json.dumps(package), encoding="utf-8")
    # How far the History row's count reaches, for analyze.py to print beside
    # the count it qualifies rather than as a line of its own above the block.
    (out_dir / "history.json").write_text(json.dumps({
        "unavailable": tape_error is not None,
        "as_of": coverage["history_as_of"],
    }), encoding="utf-8")
    # Coverage travels on BOTH paths. Reporting it only on a miss left
    # recurrence — the one figure the uncovered tail actually moves — rendered
    # as a fact while the same tail was being treated as decisive above.
    return {"fill": len(fill), "hist": len(hist),
            "blocks": len({r["BLOCK_TRADE_ID"] for r in hist if r["BLOCK_TRADE_ID"]}),
            "source": source, "notes": notes, **coverage}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rfq_id")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--fill-json", help="the trade JSON the terminal attached, as a file")
    args = parser.parse_args()
    rfq_id = args.rfq_id.strip()
    if not core_id(rfq_id):
        # Exit 2 is the documented code for a malformed id. Leaving it to raise
        # inside collect() sent it to the catch-all below, which answers a typo
        # with "execution tape unavailable" — a dead pipeline that is not dead.
        print(f"analyze: invalid rfq_id {args.rfq_id!r}", file=sys.stderr)
        return 2
    injected = None
    if args.fill_json:
        try:
            injected = fill_sources.load_injected(args.fill_json)
        except (OSError, ValueError) as exc:
            # A payload that will not parse is not a reason to stop: the tape and
            # the API are still there. Say so, and carry on without it.
            print(f"analyze: the attached trade data was not readable JSON ({exc}); "
                  "resolved without it", file=sys.stderr)
    try:
        counts = collect(rfq_id, Path(args.out_dir), injected=injected)
    except AmbiguousRfqError as exc:
        print(f"analyze: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        # A reader refusal with no other source holding the fill is a DEAD
        # PIPELINE, not an unknown RFQ. analyze.py's missing-fill message says
        # "not on the Paradigm tape", which would blame the trade for an outage.
        print(f"analyze: execution tape unavailable — {exc}", file=sys.stderr)
        return 4
    if not counts["fill"]:
        # Said to the person analysing the trade, not to whoever runs the tape:
        # when the history ends, and what that means for a trade they just did.
        as_of = counts.get("history_as_of") or ""
        api = counts.get("api_reason") or ""
        if api.startswith("not among"):
            tail = " (Also not found via Paradigm's API.)"
        elif api:
            tail = f" {api[0].upper()}{api[1:]}."
        else:
            tail = ""
        if as_of:
            print(f"analyze: Couldn't find {rfq_id}. Paradigm's history is current to {as_of}, "
                  f"so a very recent trade may not show yet.{tail}", file=sys.stderr)
            return 6
        print(f"analyze: Couldn't find {rfq_id} in Paradigm's 30-day history.{tail}",
              file=sys.stderr)
        return 5
    # A history that stops short is printed beside the count it qualifies, in
    # the History row; only what the reader must act on goes above the block.
    for note in counts.get("notes") or []:
        print(f"analyze: {note}", file=sys.stderr)
    print(f"fill={counts['fill']} hist={counts['hist']} blocks={counts['blocks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
