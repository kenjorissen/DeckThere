#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
purge=false
case "${1:-}" in
  '') [[ $# == 0 ]] || exit 1 ;;
  --purge-settings)
    [[ $# == 1 ]] || exit 1
    purge=true
    ;;
  *)
    echo 'Usage: ./uninstall.sh [--purge-settings]' >&2
    exit 1
    ;;
esac

if [[ $EUID == 0 ]]; then
  echo 'Run uninstall.sh as your normal user, not with sudo.' >&2
  exit 1
fi
USER_ROOT="${HOME:?HOME must be set}/.local/share/deckthere"
echo 'Removing the DeckThere service, installed code, and sudo rule.'
if "$purge"; then
  echo 'WARNING: --purge-settings permanently deletes /home/.deckthere, including licenses/settings.'
fi
sudo -v
# Abort on stop failure: never delete files beneath a still-running service.
if [[ $(systemctl show -p LoadState --value deckthere.service) != not-found ]]; then
  sudo systemctl stop deckthere.service
fi
sudo rm -f -- /etc/sudoers.d/zz-deckthere /etc/systemd/system/deckthere.service
sudo rm -rf -- /home/.deckthere/bin /run/deckthere /run/deckthere-launch
sudo systemctl daemon-reload
if "$purge"; then
  sudo rm -rf -- /home/.deckthere
else
  echo 'Preserved settings/license in /home/.deckthere/data.'
fi
# Remove only our known user tools, not other files someone may have put here.
rm -f -- "$USER_ROOT/deckthere.sh" "$USER_ROOT/deckthere-gui.sh" "$USER_ROOT/doctor.sh" \
  "$USER_ROOT/steam-shortcut.py" "$USER_ROOT/uninstall.sh" \
  "$USER_ROOT/deckthere-launch.sh" "$USER_ROOT/deckthere_session.py" "$USER_ROOT/deckthere_idle.py" "$USER_ROOT/deckthere_preferences.py" "$USER_ROOT/deckthere_qt.py" \
  "$USER_ROOT/deckthere_ui.py" "$USER_ROOT/deckthere_ui.qml" "$USER_ROOT/DeckThereSettings.qml" "$USER_ROOT/deckthere_settings.qml" "$USER_ROOT/deckthere_ipc.py" \
  "$USER_ROOT/deckthere_keyboard.py" "$USER_ROOT/deckthere_layouts.json" "$USER_ROOT/deckthere_dashboard.py" "$USER_ROOT/deckthere-gui-deps.py" \
  "$USER_ROOT/session.lock"
if "$purge"; then rm -f -- "$USER_ROOT/launch-mode"; fi
# This entire subtree is DeckThere's disposable GUI configuration/state/cache.
rm -rf -- "$USER_ROOT/konsole" "$USER_ROOT/pylib" "$USER_ROOT/__pycache__"
if [[ -d "$USER_ROOT" ]]; then rmdir -- "$USER_ROOT" 2>/dev/null || true; fi
echo 'Uninstalled. Remove the DeckThere non-Steam shortcut manually in Steam.'
echo 'The checkout and any old virtualhere/ files were left untouched.'
