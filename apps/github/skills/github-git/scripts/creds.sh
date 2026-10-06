# Sourced by the other scripts. pick_cred SUFFIX OVERRIDE_VAR sets $placeholder
# to the value of the one CRED_GITHUB..._SUFFIX variable, or the one OVERRIDE_VAR
# names, and exits 2 with a message the skill's failure table lists.

pick_cred() {
  suffix=$1
  override=$2
  eval "chosen=\${$override:-}"
  if [ -n "$chosen" ]; then
    case $chosen in
      CRED_GITHUB*) var=$chosen ;;
      *) echo "$override must name a CRED_GITHUB... variable" >&2; exit 2 ;;
    esac
  else
    vars=$(env | sed -n "s/^\(CRED_GITHUB[A-Z0-9_]*_$suffix\)=.*/\1/p")
    count=$(printf '%s\n' "$vars" | grep -c . || true)
    if [ "$count" -eq 0 ]; then
      echo "no GitHub $suffix credential in the environment (want CRED_GITHUB_..._$suffix)" >&2
      exit 2
    fi
    if [ "$count" -gt 1 ]; then
      echo "several GitHub $suffix credentials: $(echo $vars). Re-run with $override=<the one you want>" >&2
      exit 2
    fi
    var=$vars
  fi
  placeholder=$(printenv "$var" || true)
  if [ -z "$placeholder" ]; then
    echo "$var is not set" >&2
    exit 2
  fi
}

has_cred() {
  env | grep -q "^CRED_GITHUB[A-Z0-9_]*_$1="
}
