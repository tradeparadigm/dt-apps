#!/bin/sh
# Points plain git and gh at the GitHub placeholders. Safe to run every time:
# each run replaces what the last one wrote.
set -eu
here=$(dirname "$0")
. "$here/creds.sh"

did=

GH_VERSION=2.102.0
install_gh() {
  case $(uname -m) in
    aarch64|arm64) arch=arm64; sum=7862c86c72f43df3a2d93ddde6f473285b4e2af61b494849846827e513ef6484 ;;
    x86_64|amd64) arch=amd64; sum=bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386 ;;
    *) echo "no gh build for $(uname -m); use scripts/api.sh" >&2; exit 2 ;;
  esac
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/gh.XXXXXX")
  name=gh_${GH_VERSION}_linux_$arch
  if ! curl -fsSL --max-time 300 -o "$tmp/gh.tgz" \
      "https://github.com/cli/cli/releases/download/v$GH_VERSION/$name.tar.gz"; then
    rm -rf "$tmp"; echo "could not download gh; use scripts/api.sh" >&2; exit 2
  fi
  if [ "$(sha256sum "$tmp/gh.tgz" | cut -d' ' -f1)" != "$sum" ]; then
    rm -rf "$tmp"; echo "the gh download has the wrong sha256; not installing it" >&2; exit 2
  fi
  tar -xzf "$tmp/gh.tgz" -C "$tmp" --no-same-owner
  mv "$tmp/$name/bin/gh" "$1"
  rm -rf "$tmp"
}

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
  keep=$HOME/.openclaw
  dir=${GH_CONFIG_DIR:-$keep/gh}
  mkdir -p "$dir" "$keep/bin"
  login=$(curl -sS --max-time 20 -H "Authorization: Bearer $placeholder" https://api.github.com/user \
    | sed -n 's/^[^"]*"login": *"\([^"]*\)".*/\1/p' | head -1)
  if [ -z "$login" ]; then
    echo "could not read the token's login from api.github.com/user; gh will still work" >&2
    login=x-access-token
  fi
  (
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
  )
  chmod 600 "$dir/hosts.yml"
  [ -f "$dir/config.yml" ] || printf 'version: "1"\ngit_protocol: https\n' > "$dir/config.yml"

  # ~/.openclaw is the only directory that survives a pod restart, so gh and
  # its config go there, and the wrapper points gh at that config.
  real=$(command -v gh || true)
  [ "$real" = "$keep/bin/gh" ] && real=
  if [ -z "$real" ]; then
    real=$keep/bin/gh-$GH_VERSION
    [ -x "$real" ] || install_gh "$real"
  fi
  printf '#!/bin/sh\nGH_CONFIG_DIR="%s" exec "%s" "$@"\n' "$dir" "$real" > "$keep/bin/gh"
  chmod 755 "$keep/bin/gh"
  did="$did gh"
fi

if [ -z "$did" ]; then
  echo "no GitHub credential in the environment (want CRED_GITHUB_..._GIT or CRED_GITHUB_..._REST)" >&2
  exit 2
fi
echo "configured:$did"
case $did in *gh*) echo "gh: $HOME/.openclaw/bin/gh" ;; esac
