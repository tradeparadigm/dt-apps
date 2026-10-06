#!/bin/sh
# Calls the GitHub REST API with the placeholder as the bearer token.
# METHOD (default GET), TARGET (path and query) and BODY come from the
# environment so one file serves every call.
set -eu

. "$(dirname "$0")/creds.sh"
pick_cred REST GITHUB_REST_CRED

case ${TARGET:-} in
  /*) ;;
  *) echo "set TARGET to a path starting with /, e.g. TARGET=/user" >&2; exit 2 ;;
esac

set -- -sS -X "${METHOD:-GET}" \
  -H "Authorization: Bearer $placeholder" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -w '\nHTTP %{http_code}\n'
if [ -n "${BODY:-}" ]; then
  set -- "$@" -H "Content-Type: application/json" --data-raw "$BODY"
fi

headers=$(mktemp)
trap 'rm -f "$headers"' EXIT
curl "$@" -D "$headers" "https://api.github.com$TARGET"
grep -i -E '^(link|x-ratelimit-)' "$headers" | tr -d '\r' >&2 || true
