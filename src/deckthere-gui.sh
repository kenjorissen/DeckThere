#!/usr/bin/env bash
set -euo pipefail
if (($# != 0)); then
  echo 'Usage: deckthere-gui.sh' >&2
  exit 1
fi
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
private="$HERE/konsole"
if [[ ! -x "$HERE/deckthere.sh" || ! -f "$private/config/konsolerc" ||
  ! -f "$private/data/kxmlgui5/konsole/konsoleui.rc" ||
  ! -f "$private/data/kxmlgui5/konsole/sessionui.rc" ]]; then
  echo 'DeckThere GUI installation incomplete. Run setup.sh again.' >&2
  exit 1
fi

# Isolate Konsole only. Restore the exact original XDG environment (including
# unset versus empty variables) before starting the actual service launcher.
unset_args=()
value_args=()
for variable in XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME; do
  if [[ -v $variable ]]; then
    value_args+=("$variable=${!variable}")
  else
    unset_args+=(-u "$variable")
  fi
done
mkdir -p -- "$private/state" "$private/cache"
exec /usr/bin/env -u LD_PRELOAD \
  XDG_CONFIG_HOME="$private/config" XDG_DATA_HOME="$private/data" \
  XDG_STATE_HOME="$private/state" XDG_CACHE_HOME="$private/cache" \
  konsole --separate --fullscreen --hide-menubar --hide-tabbar \
  -p ScrollBarPosition=2 -e /usr/bin/env "${unset_args[@]}" "${value_args[@]}" "$HERE/deckthere.sh"
