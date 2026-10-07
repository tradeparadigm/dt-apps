#!/usr/bin/env bash
# analyze.sh — the entire block analysis in one command, so the agent types one
# short line instead of orchestrating multi-round fetches and greek reasoning by
# hand. Does: resolve the RFQ off the execution tape (collect_analysis.py) →
# analyze.py (concurrent Deribit fetch, net greeks, render). Its stdout IS the
# finished block.
#
# The STS bootstrap and the inline DuckDB scan are gone: the shared reader in
# paradigm-data-discovery resolves credentials through the chain, so no keys are written
# to a temp SQL file, and it reads the daily execution partitions rather than
# hot__paradigm_trade_tape_30d.
#
# Exit codes are instructions to whoever runs it, nothing more:
#   0  stdout is the answer: a block (saying what could not be fetched, if
#      anything), or a one-line message (not found, bad id). Relay it as is.
#   1  stdout needs an agent: it ends with a "## For the agent" section saying
#      what to do and carrying every number already pulled.
# The reasons behind each (collect_analysis.py's own codes) stay in here.
#
# Usage: bash scripts/analyze.sh <rfq_id> [--fill-json FILE|-]
#   e.g. analyze.sh r_3FvzJWGF…
# --fill-json hands over the trade JSON the terminal attached to the message (a
# file, or `-` for stdin). The tape does not hold a block for up to an hour after
# it prints, and this is what the script resolves the fill from in that window.
set -uo pipefail

RAW="${1:-}"
[ -z "$RAW" ] && { echo "analyze: usage: /analyze <rfq_id> — an r_… id from the Paradigm tape."; exit 0; }
shift
# Only the ID is authoritative; any <rfq description> after it is ignored here.
FILL_JSON=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fill-json) FILL_JSON="${2:-}"; shift 2 || shift ;;
    --fill-json=*) FILL_JSON="${1#--fill-json=}"; shift ;;
    *) shift ;;
  esac
done
CORE=$(printf '%s' "$RAW" | sed -E 's/^(DRFQv2-|GRFQ-)//')
case "$CORE" in
  ''|*[!A-Za-z0-9_-]*) echo "analyze: invalid rfq_id — expected an r_… id (letters/digits/_/- only)"; exit 0 ;;
esac
DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Testability hook: print the resolved core id and exit (no creds/network).
[ -n "${ANALYZE_PRINT_ID:-}" ] && { echo "$CORE"; exit 0; }

OUT=$(mktemp -d "${TMPDIR:-/tmp}/analyze.XXXXXX")
trap 'rm -rf "$OUT"' EXIT

# Stdin is read here, once, so collect gets a file whichever way it arrived.
FILL_ARGS=()
if [ "$FILL_JSON" = "-" ]; then
  cat > "$OUT/.fill-input.json"
  FILL_ARGS=(--fill-json "$OUT/.fill-input.json")
elif [ -n "$FILL_JSON" ]; then
  FILL_ARGS=(--fill-json "$FILL_JSON")
fi

# Resolve FIRST and stop on failure. analyze.py reports a missing fill.csv as
# "RFQ not resolved (not on Paradigm tape)", which would blame the trade for a
# producer outage — so a reader refusal has to surface as itself, here.
# collect's own stderr IS the message, relayed onto stdout unchanged. Re-wording
# it here produced two differently worded lines for one failure — and the arm for
# exit 6 still said "Retry after the next sync" after collect_analysis.py had been
# rewritten to stop saying exactly that. One author, one sentence.
# `status=$?` after `if ! cmd` reads the NEGATION's result (always 0), which
# would send every failure to the default branch. Capture, then test.
# collect's stderr goes to a FILE, not through `2>&1 >/dev/null`: that captures
# uv's stderr too, so a cold package cache put "Installed 13 packages in 106ms"
# on stdout above the block — and SKILL.md tells the model stdout is its entire
# reply. Only lines collect_analysis.py itself authored are relayed.
# Each step has its own deadline, together under the 90s the terminal allows a
# run, so a hang ends here with what it has rather than being killed with none.
COLLECT_S="${ANALYZE_COLLECT_TIMEOUT:-50}"
RENDER_S="${ANALYZE_RENDER_TIMEOUT:-30}"
deadline() { if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }

# The agent's part of an exit-1 output, in the same shape analyze.py writes it.
handoff() {  # <task> <rules> [data file]
  printf '\n## For the agent\n\n%s\n\n%s\n' "$1" "$2"
  if [ -n "${3:-}" ] && [ -s "$3" ]; then
    printf '\n```csv\n'; cat "$3"; printf '```\n'
  fi
}

err="$OUT/.collect.err"
deadline "$COLLECT_S" uv run "$DIR/scripts/collect_analysis.py" "$RAW" --out-dir "$OUT" ${FILL_ARGS[@]+"${FILL_ARGS[@]}"} >/dev/null 2>"$err"
status=$?
note=$(grep '^analyze: ' "$err" 2>/dev/null)
case "$status" in
  0) ;;
  2|3|4|5|6)
    # collect's own answers (bad id, ambiguous id, history down, not found):
    # one line, written for the user. Nothing an agent could add.
    printf '%s\n' "$note"
    exit 0 ;;
  *)
    # collect never got to answer: a crash, uv, or the deadline. The whole
    # analysis is undone, so this is the one case for a rebuild by hand.
    if [ "$status" -eq 124 ]; then
      why="reading Paradigm's history took longer than ${COLLECT_S}s"
    else
      why="collect_analysis.py failed (exit $status) before resolving the trade"
    fi
    printf 'analyze: the analysis could not run — %s.\n' "$why"
    [ -n "$note" ] && printf '%s\n' "$note"
    tail=$(grep -vE '^ *(Installed|Uninstalled|Downloading|Downloaded|Resolved|Prepared|Audited) |^analyze: ' "$err" 2>/dev/null | tail -n 5)
    [ -n "$tail" ] && printf '%s\n' "$tail"
    if [ ${#FILL_ARGS[@]} -gt 0 ]; then
      task="Build the analysis by hand from the trade data attached to the message, as paradigm-block-analyst Steps 1–7 describe, and say it was built by hand."
    else
      task="Build the analysis by hand as paradigm-block-analyst Steps 1–7 describe, resolving the trade with the manual recipe in references/rfq-lookup.md, and say it was built by hand. If that cannot resolve it either, reply with the analyze: line above."
    fi
    handoff "$task" "Do not run analyze.sh again: it has just failed."
    exit 1 ;;
esac
# Exit 0 can still carry a note (legs that do not net to the package price). It
# is part of the answer, so it goes to stdout with the block rather than being
# swallowed — followed by a blank line, or markdown joins it to the header.
[ -n "$note" ] && printf '%s\n\n' "$note"

# Why this block matters to the user (interest.py): one line or nothing, asked
# of JEV beside the render so it costs no time of its own. It reads the fill
# collect just wrote, and it never fails the analysis.
interest="$OUT/.interest.out"
(cd "$DIR" && deadline "${ANALYZE_INTEREST_TIMEOUT:-15}" uv run scripts/interest.py --fill-csv "$OUT/fill.csv") \
  >"$interest" 2>/dev/null &
interest_pid=$!

# No exec — the EXIT trap must survive to clean the CSVs after the render.
# analyze.py exits 0 (the answer) or 1 (with its own section for the agent).
# Anything else, or a 1 without that section, is the render dying: the trade is
# resolved, so the agent gets the rows to finish from.
out="$OUT/.render.out"
(cd "$DIR" && deadline "$RENDER_S" uv run scripts/analyze.py --csv-dir "$OUT" --render) >"$out" 2>"$OUT/.render.err"
status=$?
if [ "$status" -eq 0 ] || { [ "$status" -eq 1 ] && grep -q '^## For the agent$' "$out"; }; then
  wait "$interest_pid" 2>/dev/null
  line=$(head -n 1 "$interest" 2>/dev/null)
  if [ -z "$line" ]; then
    cat "$out"
  elif [ "$status" -eq 0 ]; then
    cat "$out"; printf '\n%s\n' "$line"
  else
    # The line belongs to the block, above the agent's section.
    awk -v line="$line" '/^## For the agent$/ && !done { print line; print ""; done = 1 } { print }' "$out"
  fi
  exit "$status"
fi
kill "$interest_pid" 2>/dev/null
cat "$out"
if [ "$status" -eq 124 ]; then
  printf 'analyze: fetching live data took longer than %ss.\n' "$RENDER_S"
else
  printf 'analyze: analyze.py failed (exit %s) after the trade was found.\n' "$status"
  grep -vE '^ *(Installed|Uninstalled|Downloading|Downloaded|Resolved|Prepared|Audited) ' "$OUT/.render.err" 2>/dev/null | tail -n 3
fi
handoff "Build the block from the trade rows below, as paradigm-block-analyst Steps 1–7 describe: legs from the rows (never the user's text), each leg's live ticker on the trade's venue, the net greeks, and the fill against the mark." \
  "Fetch only the live data the block needs; the trade itself is below." "$OUT/fill.csv"
exit 1
