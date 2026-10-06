#!/bin/sh
# Points plain git and gh at the GitHub placeholders. Safe to run every time:
# each run replaces what the last one wrote.
set -eu
here=$(dirname "$0")
. "$here/creds.sh"

did=

if [ -n "${GITHUB_GIT_CRED:-}" ] || has_cred GIT; then
  pick_cred GIT GITHUB_GIT_CRED
  basic=$(printf 'x-access-token:%s' "$placeholder" | base64 | tr -d '\n')
  git config --global --replace-all http.https://github.com/.extraHeader "Authorization: Basic $basic"
  git config --global --unset-all url.https://github.com/.insteadOf || true
  git config --global --add url.https://github.com/.insteadOf git@github.com:
  git config --global --add url.https://github.com/.insteadOf ssh://git@github.com/
  did="$did git"
fi

if [ -n "${GITHUB_REST_CRED:-}" ] || has_cred REST; then
  pick_cred REST GITHUB_REST_CRED
  dir=${GH_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/gh}
  if [ -L "$dir" ]; then mkdir -p "$(readlink "$dir")"; else mkdir -p "$dir"; fi
  login=$(curl -sS --max-time 20 -H "Authorization: Bearer $placeholder" https://api.github.com/user \
    | sed -n 's/^[^"]*"login": *"\([^"]*\)".*/\1/p' | head -1)
  if [ -z "$login" ]; then
    echo "could not read the token's login from api.github.com/user; gh will still work" >&2
    login=x-access-token
  fi
  umask 077
  cat > "$dir/hosts.yml" <<EOF
github.com:
    oauth_token: "$placeholder"
    git_protocol: https
    user: "$login"
    users:
        "$login":
            oauth_token: "$placeholder"
EOF
  chmod 600 "$dir/hosts.yml"
  [ -f "$dir/config.yml" ] || printf 'version: "1"\ngit_protocol: https\n' > "$dir/config.yml"
  did="$did gh"
fi

if [ -z "$did" ]; then
  echo "no GitHub credential in the environment (want CRED_GITHUB_..._GIT or CRED_GITHUB_..._REST)" >&2
  exit 2
fi
echo "configured:$did"
