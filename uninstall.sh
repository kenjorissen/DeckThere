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
USER_ROOT="${HOME:?HOME must be set}/.local/share/VirtualHerePad"
echo 'Removing the VHP service, installed code, and sudo rule.'
if "$purge"; then
  echo 'WARNING: --purge-settings permanently deletes /home/.vhp and /var/lib/vhp, including licenses/settings.'
fi
sudo -v
# Abort on stop failure: never delete files beneath a still-running service.
if [[ $(systemctl show -p LoadState --value vhp.service) != not-found ]]; then
  sudo systemctl stop vhp.service
fi
sudo rm -f -- /etc/sudoers.d/zz-vhp /etc/sudoers.d/vhp /etc/systemd/system/vhp.service
sudo rm -rf -- /home/.vhp/bin /run/vhp /run/vhp-launch
sudo systemctl daemon-reload
if "$purge"; then
  sudo rm -rf -- /home/.vhp /var/lib/vhp
else
  echo 'Preserved settings/license in /home/.vhp/data (and any previous config copies).'
fi
# Remove only our known user tools, not other files someone may have put here.
rm -f -- "$USER_ROOT/vhp.sh" "$USER_ROOT/vhp-gui.sh" "$USER_ROOT/doctor.sh" \
  "$USER_ROOT/steam-shortcut.py" "$USER_ROOT/uninstall.sh" \
  "$USER_ROOT/vhp-launch.sh" "$USER_ROOT/vhp_session.py" "$USER_ROOT/vhp_idle.py" "$USER_ROOT/vhp_preferences.py" "$USER_ROOT/vhp_qt.py" \
  "$USER_ROOT/vhp_ui.py" "$USER_ROOT/vhp_ui.qml" "$USER_ROOT/VhpSettings.qml" "$USER_ROOT/vhp_settings.qml" "$USER_ROOT/vhp_ipc.py" \
  "$USER_ROOT/vhp_keyboard.py" "$USER_ROOT/vhp_layouts.json" "$USER_ROOT/vhp_dashboard.py" "$USER_ROOT/vhp-gui-deps.py" \
  "$USER_ROOT/session.lock"
if "$purge"; then rm -f -- "$USER_ROOT/launch-mode"; fi
# This entire subtree is VHP's disposable GUI configuration/state/cache.
rm -rf -- "$USER_ROOT/konsole" "$USER_ROOT/pylib" "$USER_ROOT/__pycache__"
if [[ -d "$USER_ROOT" ]]; then rmdir -- "$USER_ROOT" 2>/dev/null || true; fi
echo 'Uninstalled. Remove the VirtualHerePad non-Steam shortcut manually in Steam.'
echo 'The checkout and any old virtualhere/ files were left untouched.'
