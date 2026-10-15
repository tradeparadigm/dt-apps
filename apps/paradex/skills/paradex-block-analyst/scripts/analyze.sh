#!/usr/bin/env bash
# analyze.sh — the whole Option Monitor block analysis in one command: resolve
# the block (collect.py), then benchmark and render it (analyze.py). Its stdout
# IS the finished block.
#
# Exit codes are instructions to whoever runs it, nothing more:
#   0  stdout is the answer: a block (saying what could not be fetched, if
#      anything), or a one-line message (not found, bad reference). Relay it.
#   1  stdout needs an agent: it ends with a "## For the agent" section saying
#      what to do and carrying every number already pulled.
#
# Usage: bash scripts/analyze.sh <VENUE:block_id> [--fill-json FILE|-]
#   e.g. analyze.sh DBT:BLOCK-302780
# --fill-json hands over the JSON the terminal attached to the message (a file,
# or `-` for stdin): the block the user clicked, and the tape beside it.
set -uo pipefail

RAW="${1:-}"
[ -z "$RAW" ] && { echo "analyze: usage: /paradex_analyze <VENUE:block_id> — a block from the Option Monitor."; exit 0; }
shift
# Only the reference is authoritative; any name after it is ignored here.
FILL_JSON=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fill-json) FILL_JSON="${2:-}"; shift 2 || shift ;;
    --fill-json=*) FILL_JSON="${1#--fill-json=}"; shift ;;
    *) shift ;;
  esac
done
# The same rule as collect.py's, here too so nothing that looks like a flag
# ever reaches a script's argv.
if ! [[ "$RAW" =~ ^[A-Z]{2,10}:[A-Za-z0-9._-]{1,128}$ ]]; then
  echo "analyze: expected a block reference like DBT:BLOCK-302780 — the venue, a colon, and the block id."
  exit 0
fi
DIR="$(cd "$(dirname "$0")/.." && pwd)"

OUT=$(mktemp -d "${TMPDIR:-/tmp}/paradex-analyze.XXXXXX")
trap 'rm -rf "$OUT"' EXIT

# Stdin is read here, once, so collect gets a file whichever way it arrived.
FILL_ARGS=()
if [ "$FILL_JSON" = "-" ]; then
  cat > "$OUT/.fill-input.json"
  FILL_ARGS=(--fill-json "$OUT/.fill-input.json")
elif [ -n "$FILL_JSON" ]; then
  FILL_ARGS=(--fill-json "$FILL_JSON")
fi

# Each step has its own deadline, together under the 90s the terminal allows a
# run, so a hang ends here with what it has rather than being killed with none.
COLLECT_S="${ANALYZE_COLLECT_TIMEOUT:-40}"
RENDER_S="${ANALYZE_RENDER_TIMEOUT:-30}"
deadline() { if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }

handoff() {  # <task> <rules> [data file]
  printf '\n## For the agent\n\n%s\n\n%s\n' "$1" "$2"
  if [ -n "${3:-}" ] && [ -s "$3" ]; then
    printf '\n```json\n'; cat "$3"; printf '\n```\n'
  fi
}

# collect's stderr goes to a file: only the lines it wrote for the user
# (`analyze: …`) are relayed, never uv's own chatter.
err="$OUT/.collect.err"
deadline "$COLLECT_S" uv run "$DIR/scripts/collect.py" "$RAW" --out-dir "$OUT" ${FILL_ARGS[@]+"${FILL_ARGS[@]}"} >/dev/null 2>"$err"
status=$?
note=$(grep '^analyze: ' "$err" 2>/dev/null)
case "$status" in
  0) ;;
  2|4|5)
    # collect's own answers (bad reference, tape down, not found): one line,
    # written for the user. Nothing an agent could add.
    printf '%s\n' "$note"
    exit 0 ;;
  *)
    if [ "$status" -eq 124 ]; then
      why="reading Paradex's options tape took longer than ${COLLECT_S}s"
    else
      why="collect.py failed (exit $status) before resolving the block"
    fi
    printf 'analyze: the analysis could not run — %s.\n' "$why"
    tail=$(grep -vE '^ *(Installed|Uninstalled|Downloading|Downloaded|Resolved|Prepared|Audited) |^analyze: ' "$err" 2>/dev/null | tail -n 5)
    [ -n "$tail" ] && printf '%s\n' "$tail"
    if [ ${#FILL_ARGS[@]} -gt 0 ]; then
      task="Build the analysis by hand from the block attached to the message, as paradex-block-analyst describes, and say it was built by hand."
    else
      task="Build the analysis by hand as paradex-block-analyst describes, finding the block on Paradex's options tape, and say it was built by hand."
    fi
    handoff "$task" "Do not run analyze.sh again: it has just failed."
    exit 1 ;;
esac

# What the taker is betting on (interest.py): one line or nothing, asked of JEV
# beside the render. Once the render is done it gets ANALYZE_INTEREST_GRACE
# seconds more: a slow JEV costs the line, never the user's wait.
interest="$OUT/.interest.out"
(cd "$DIR" && deadline "${ANALYZE_INTEREST_TIMEOUT:-15}" uv run scripts/interest.py --block-json "$OUT/block.json") \
  >"$interest" 2>/dev/null &
interest_pid=$!

out="$OUT/.render.out"
(cd "$DIR" && deadline "$RENDER_S" uv run scripts/analyze.py --dir "$OUT" --render) >"$out" 2>"$OUT/.render.err"
status=$?
if [ "$status" -eq 0 ] || { [ "$status" -eq 1 ] && grep -q '^## For the agent$' "$out"; }; then
  grace=$(( ${ANALYZE_INTEREST_GRACE:-3} * 10 ))
  while [ "$grace" -gt 0 ] && kill -0 "$interest_pid" 2>/dev/null; do
    sleep 0.1; grace=$((grace - 1))
  done
  line=""
  if kill -0 "$interest_pid" 2>/dev/null; then
    pkill -P "$interest_pid" 2>/dev/null; kill "$interest_pid" 2>/dev/null
  else
    wait "$interest_pid" 2>/dev/null
    line=$(head -n 1 "$interest" 2>/dev/null)
  fi
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
pkill -P "$interest_pid" 2>/dev/null; kill "$interest_pid" 2>/dev/null
cat "$out"
if [ "$status" -eq 124 ]; then
  printf 'analyze: fetching live data took longer than %ss.\n' "$RENDER_S"
else
  printf 'analyze: analyze.py failed (exit %s) after the block was found.\n' "$status"
  grep -vE '^ *(Installed|Uninstalled|Downloading|Downloaded|Resolved|Prepared|Audited) ' "$OUT/.render.err" 2>/dev/null | tail -n 3
fi
handoff "Build the analysis from the block below, as paradex-block-analyst describes: each leg's live ticker, the net greeks, and the fill against the mark." \
  "Fetch only the live data the analysis needs; the block itself is below." "$OUT/block.json"
exit 1
