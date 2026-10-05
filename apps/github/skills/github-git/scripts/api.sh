#!/bin/sh
# Calls the GitHub REST API with the placeholder as the bearer token.
# METHOD (default GET), TARGET (path and query) and BODY come from the
# environment so one file serves every call.
set -eu

if [ -n "${GITHUB_REST_CRED:-}" ]; then
  case $GITHUB_REST_CRED in
    CRED_GITHUB*) var=$GITHUB_REST_CRED ;;
    *) echo "GITHUB_REST_CRED must name a CRED_GITHUB... variable" >&2; exit 2 ;;
  esac
else
  vars=$(env | sed -n 's/^\(CRED_GITHUB[A-Z0-9_]*_REST\)=.*/\1/p')
  count=$(printf '%s\n' "$vars" | grep -c . || true)
  if [ "$count" -eq 0 ]; then
    echo "no GitHub REST credential in the environment (want CRED_GITHUB_..._REST)" >&2
    exit 2
  fi
  if [ "$count" -gt 1 ]; then
    echo "several GitHub REST credentials: $(echo $vars). Re-run with GITHUB_REST_CRED=<the one you want>" >&2
    exit 2
  fi
  var=$vars
fi

placeholder=$(printenv "$var" || true)
if [ -z "$placeholder" ]; then
  echo "$var is not set" >&2
  exit 2
fi
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

exec curl "$@" "https://api.github.com$TARGET"
