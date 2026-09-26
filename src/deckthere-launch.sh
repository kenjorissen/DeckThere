#!/usr/bin/env bash
# The one installed Steam target; no privilege or checkout dependency here.
set -euo pipefail
unset LD_PRELOAD
base=$(dirname -- "$(readlink -f -- "$0")")
mode=''
keyboard=false
for option in "$@"; do
  case "$option" in
    --gui | --terminal)
      [[ -z $mode ]] || {
        echo 'Choose one interface.' >&2
        exit 1
      }
      mode=${option#--}
      ;;
    --keyboard) keyboard=true ;;
    *)
      echo 'Usage: deckthere-launch.sh [--terminal | --gui [--keyboard]]' >&2
      exit 1
      ;;
  esac
done
if "$keyboard"; then
  [[ $mode != terminal ]] || {
    echo 'Keyboard requires GUI mode.' >&2
    exit 1
  }
  mode=keyboard # Also accepts the legacy --keyboard spelling.
fi
if [[ -z $mode ]]; then
  mode=$(/usr/bin/python3 -I "$base/deckthere_preferences.py" "$base/launch-mode")
fi
exec 9>"$base/session.lock"
flock -n 9 || {
  echo 'DeckThere is already open.' >&2
  exit 1
}
if [[ $mode == terminal ]]; then exec "$base/deckthere-gui.sh"; fi
options=()
[[ $mode != keyboard ]] || options+=(--keyboard)
exec /usr/bin/python3 -I "$base/deckthere_session.py" "${options[@]}"
