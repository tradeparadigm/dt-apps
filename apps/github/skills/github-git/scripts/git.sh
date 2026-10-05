#!/bin/sh
# Runs git with the GitHub placeholder sent as Basic auth on every request.
# git sends no credentials until it sees a 401, and the proxy answers an
# unauthenticated fetch with a 403, so the header has to go on the first one.
set -eu

if [ -n "${GITHUB_GIT_CRED:-}" ]; then
  case $GITHUB_GIT_CRED in
    CRED_GITHUB*) var=$GITHUB_GIT_CRED ;;
    *) echo "GITHUB_GIT_CRED must name a CRED_GITHUB... variable" >&2; exit 2 ;;
  esac
else
  vars=$(env | sed -n 's/^\(CRED_GITHUB[A-Z0-9_]*_GIT\)=.*/\1/p')
  count=$(printf '%s\n' "$vars" | grep -c . || true)
  if [ "$count" -eq 0 ]; then
    echo "no GitHub git credential in the environment (want CRED_GITHUB_..._GIT)" >&2
    exit 2
  fi
  if [ "$count" -gt 1 ]; then
    echo "several GitHub git credentials: $(echo $vars). Re-run with GITHUB_GIT_CRED=<the one you want>" >&2
    exit 2
  fi
  var=$vars
fi

placeholder=$(printenv "$var" || true)
if [ -z "$placeholder" ]; then
  echo "$var is not set" >&2
  exit 2
fi
basic=$(printf 'x-access-token:%s' "$placeholder" | base64 | tr -d '\n')

GIT_TERMINAL_PROMPT=0 exec git \
  -c credential.helper= \
  -c "http.https://github.com/.extraHeader=Authorization: Basic $basic" \
  "$@"
