#!/usr/bin/env bash
# Compatibility entry point. The prototype now uses the installed launcher.
set -euo pipefail
launcher="${HOME:?HOME must be set}/.local/share/deckthere/deckthere-launch.sh"
if [[ ! -x $launcher ]]; then
  echo 'Run ./setup.sh --keyboard once, then use the DeckThere Steam shortcut.' >&2
  exit 1
fi
case "${1:-}" in
  '')
    [[ $# == 0 ]] || exit 1
    exec "$launcher" --keyboard
    ;;
  --stop)
    [[ $# == 1 ]] || exit 1
    exec sudo -n /home/.deckthere/bin/deckthere-root stop
    ;;
  *)
    echo 'Usage: deckthere-gui-sandbox.sh [--stop]' >&2
    exit 1
    ;;
esac
