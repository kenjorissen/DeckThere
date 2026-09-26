#!/usr/bin/env bash
# Run as your normal Steam Deck user; sudo is used only for installation.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
cd -- "$(dirname -- "$(readlink -f -- "$0")")"

# BEGIN VIRTUALHERE_SOURCES
url=https://www.virtualhere.com/sites/default/files/usbserver/vhusbdx86_64
checksum_url=https://www.virtualhere.com/sites/default/files/usbserver/SHA1SUM
# END VIRTUALHERE_SOURCES

# BEGIN DOWNLOAD_OPTIONS
server_path=${VHP_SERVER_PATH:-}
mode=''
keyboard=false
for option in "$@"; do
  case "$option" in
    --gui | --terminal)
      [[ -z $mode ]] || {
        echo 'Choose one UI mode.' >&2
        exit 1
      }
      mode=${option#--}
      ;;
    --keyboard) keyboard=true ;;
    --manual-download)
      server_path=${server_path:-${HOME:?HOME must be set}/Downloads/vhusbdx86_64}
      ;;
    --help | -h)
      echo 'Usage: ./setup.sh [--terminal | --gui [--keyboard]] [--manual-download]'
      echo 'Fresh installs default to GUI without a keyboard. Existing choices are preserved.'
      echo 'Sharing controller and keyboard together requires a VirtualHere license.'
      echo 'Default: check the live official SHA1SUM; reuse a matching installed server or download and verify it.'
      echo 'Manual: use ~/Downloads/vhusbdx86_64 without downloading; verify it yourself first.'
      echo 'VHP_SERVER_PATH selects another local binary (also skips downloading).'
      echo 'Manual GUI setup requires an existing Qt runtime or VHP_QT_PATH.'
      exit 0
      ;;
    *)
      echo 'Usage: ./setup.sh [--terminal | --gui [--keyboard]] [--manual-download]' >&2
      exit 1
      ;;
  esac
done
if "$keyboard"; then
  [[ $mode != terminal ]] || {
    echo 'Keyboard requires GUI mode.' >&2
    exit 1
  }
  mode=keyboard # Legacy --keyboard is still accepted as GUI + keyboard.
fi
if [[ -n $server_path ]]; then
  echo 'WARNING: manual mode does not automatically verify the upstream checksum.' >&2
  echo "Verify the executable against $checksum_url before installing it." >&2
  if [[ ! -f $server_path || ! -r $server_path ]]; then
    echo 'No download will be made. Supply the generic Linux x86-64 server:' >&2
    printf '  Download: %s\n  Save as: %s\n' "$url" "$server_path" >&2
    echo 'Owner-readable permissions (0600) are sufficient; no executable bit is needed.' >&2
    echo 'Then rerun the same setup command. No hash file is required in manual mode.' >&2
    exit 1
  fi
fi
# END DOWNLOAD_OPTIONS

if [[ $EUID == 0 ]]; then
  echo 'Run ./setup.sh as your normal user, not with sudo.' >&2
  exit 1
fi
[[ $(uname -m) == x86_64 ]] || {
  echo 'An x86-64 Steam Deck is required.' >&2
  exit 1
}
# BEGIN MODE_SELECTION
USER_ROOT="${HOME:?HOME must be set}/.local/share/VirtualHerePad"
if [[ -z $mode ]]; then
  mode=$(python3 -I src/vhp_preferences.py "$USER_ROOT/launch-mode")
fi
# END MODE_SELECTION
user=$(id -un)
[[ $user =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || {
  echo 'Unsupported username.' >&2
  exit 1
}
echo '== Preflight checks =='
for cmd in curl sudo systemctl systemd-inhibit visudo install sha1sum sha256sum konsole python3 flock; do
  command -v "$cmd" >/dev/null || {
    echo "Missing dependency: $cmd" >&2
    exit 1
  }
done
if [[ -n ${VHP_SHA256:-} && ! $VHP_SHA256 =~ ^[[:xdigit:]]{64}$ ]]; then
  echo 'Invalid VHP_SHA256.' >&2
  exit 1
fi
[[ -d /run/systemd/system ]] || {
  echo 'A running systemd system is required.' >&2
  exit 1
}
# Check the user-side install location without using sudo.
user_parent=$USER_ROOT
while [[ ! -d "$user_parent" ]]; do user_parent=$(dirname "$user_parent"); done
user_probe=$(mktemp "$user_parent/.vhp-write-check.XXXXXX")
rm -f -- "$user_probe"
echo 'Checking sudo access (set a password with passwd first if needed)...'
sudo -v
# Probe the nearest existing install directories without creating installation data.
sudo bash <<'VHP_PREFLIGHT'
set -euo pipefail
for target in /home/.vhp/bin /home/.vhp/data /etc/systemd/system /etc/sudoers.d; do
  directory=$target
  while [[ ! -d "$directory" ]]; do directory=$(dirname "$directory"); done
  if ! probe=$(mktemp "$directory/.vhp-write-check.XXXXXX"); then
    echo "Cannot write installation path: $target. Check filesystem permissions/mounts." >&2
    exit 1
  fi
  rm -f -- "$probe"
done
VHP_PREFLIGHT
if ! python3 tools/steam-shortcut.py --check; then
  echo 'WARNING: Steam account setup needs attention; installation can continue without a shortcut.'
  echo 'Log into Steam once, or use --account ID with tools/steam-shortcut.py if prompted.'
fi
echo 'Preflight passed. No Steam processes were stopped.'
echo 'Display note: VHP maintains its selected brightness while running; disable Steam adaptive brightness to avoid competing changes.'
echo 'The adaptive brightness setting is not checked or changed.'
echo 'Gaming Mode: VHP uses activity pulses to prevent automatic dim/sleep without changing your timeouts. Exit VHP before requesting sleep.'
for cmd in xprop pgrep; do
  if ! command -v "$cmd" >/dev/null; then
    echo "WARNING: missing $cmd; Gaming Mode idle protection will be unavailable. See README: Gaming Mode idle handling." >&2
  fi
done

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
# BEGIN SERVER_DOWNLOAD
expected_sha1=not-verified
verification_source=manual-unverified
if [[ -n $server_path ]]; then
  echo 'Using local VirtualHere server (no downloads or upstream checksum verification).'
  [[ -f $server_path && -r $server_path ]] || {
    echo 'Local server file is not readable.' >&2
    exit 1
  }
  cp -- "$server_path" "$tmp/vhusbdx86_64"
else
  echo 'Checking the current official VirtualHere SHA1SUM over HTTPS.'
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --retry 3 --connect-timeout 20 --max-time 180 --max-filesize 65536 \
    --output "$tmp/SHA1SUM" "$checksum_url"
  # Parse data only, select exactly one exact filename, and never trust manifest paths.
  expected_sha1=$(
    python3 -I - "$tmp/SHA1SUM" <<'VHP_CHECKSUM'
import re
import sys

try:
    with open(sys.argv[1], "rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("SHA1SUM exceeds 64 KiB")
    matches = []
    for line in data.decode("ascii").splitlines():
        if not line.strip():
            continue
        record = re.fullmatch(r"([0-9a-fA-F]{40}) [ *](\S+)", line)
        if record is None:
            raise ValueError("malformed SHA1SUM entry")
        if record[2] == "vhusbdx86_64":
            matches.append(record[1].lower())
    if len(matches) != 1:
        raise ValueError("expected exactly one vhusbdx86_64 checksum")
    print(matches[0])
except (OSError, ValueError) as exc:
    print(f"Invalid VirtualHere SHA1SUM: {exc}", file=sys.stderr)
    sys.exit(1)
VHP_CHECKSUM
  )
  installed_server=/home/.vhp/bin/vhusbdx86_64
  # Verify a private copy, not a file that could change between hashing and use.
  # A missing, unreadable, or outdated installed binary falls back to download.
  if [[ -f $installed_server && -r $installed_server && ! -L $installed_server ]] &&
    cp -- "$installed_server" "$tmp/vhusbdx86_64" &&
    printf '%s  %s\n' "$expected_sha1" "$tmp/vhusbdx86_64" | sha1sum --check --status -; then
    echo 'Installed VirtualHere server matches the live checksum; reusing it without downloading the binary.'
  else
    echo 'Downloading the current VirtualHere server over HTTPS.'
    curl --fail --location --proto '=https' --proto-redir '=https' \
      --retry 3 --connect-timeout 20 --max-time 180 \
      --output "$tmp/vhusbdx86_64" "$url"
  fi
fi
[[ -s "$tmp/vhusbdx86_64" ]] || {
  echo 'Empty server file.' >&2
  exit 1
}
if [[ -z $server_path ]]; then
  if ! printf '%s  %s\n' "$expected_sha1" "$tmp/vhusbdx86_64" | sha1sum --check -; then
    echo 'VirtualHere checksum mismatch. Installation aborted; the running service is unchanged.' >&2
    echo 'Obtain the matching server and SHA1SUM directly from VirtualHere, then retry.' >&2
    exit 1
  fi
  verification_source=upstream-sha1
fi
if [[ -n ${VHP_SHA256:-} ]]; then
  [[ $VHP_SHA256 =~ ^[[:xdigit:]]{64}$ ]] || {
    echo 'Invalid VHP_SHA256.' >&2
    exit 1
  }
  printf '%s  %s\n' "$VHP_SHA256" "$tmp/vhusbdx86_64" | sha256sum --check -
  if [[ -n $server_path ]]; then
    verification_source='user-sha256'
  else verification_source='upstream-sha1+user-sha256'; fi
fi
if [[ $verification_source == manual-unverified ]]; then
  echo 'WARNING: installing a manually supplied executable without automatic checksum verification.' >&2
else
  echo "Verified VirtualHere ($verification_source)."
fi
# END SERVER_DOWNLOAD

# Resolve Qt before stopping an existing session or installing privileged code.
# Manual VirtualHere mode is also offline for Qt: never make a surprise download.
qt_source=''
if [[ $mode != terminal ]]; then
  qt_source=${VHP_QT_PATH:-$USER_ROOT/pylib}
  if ! python3 -I tools/vhp-gui-deps.py --check --destination "$qt_source"; then
    if [[ -n $server_path || -n ${VHP_QT_PATH:-} ]]; then
      echo 'A verified matching Qt runtime is required for offline GUI setup.' >&2
      echo 'Fetch it first with: python3 tools/vhp-gui-deps.py (or select --terminal).' >&2
      exit 1
    fi
    qt_source="$tmp/pylib"
    python3 -I tools/vhp-gui-deps.py --destination "$qt_source"
  fi
fi

commit=unknown
if command -v git >/dev/null && [[ $(git rev-parse --show-toplevel 2>/dev/null || true) == "$PWD" ]]; then
  commit=$(git rev-parse --verify HEAD 2>/dev/null || echo unknown)
  if [[ -n $(git status --porcelain) ]]; then commit="${commit}-dirty"; fi
fi
binary_hash=$(sha256sum "$tmp/vhusbdx86_64")
printf 'VHP_COMMIT=%s\nVIRTUALHERE_SHA256=%s\nINSTALLED_UTC=%s\n' \
  "$commit" "${binary_hash%% *}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$tmp/build-info.txt"
printf 'VIRTUALHERE_SHA1=%s\nVIRTUALHERE_VERIFICATION=%s\n' \
  "$expected_sha1" "$verification_source" >>"$tmp/build-info.txt"

printf '%s ALL=(root) NOPASSWD: /home/.vhp/bin/vhp-root start, /home/.vhp/bin/vhp-root start-gui, /home/.vhp/bin/vhp-root start-keyboard, /home/.vhp/bin/vhp-root stop, /home/.vhp/bin/vhp-root keepalive, /home/.vhp/bin/vhp-root check\n' "$user" >"$tmp/sudoers"
visudo -cf "$tmp/sudoers"
sudo -v
# Reinstalling stops the old instance first so it can restore brightness.
if systemctl is-active --quiet vhp.service; then
  sudo systemctl stop vhp.service
fi
# Persistent SteamOS code/data lives on /home, outside the user's writable home.
sudo bash <<'VHP_DATA_SETUP'
set -euo pipefail
base=/home/.vhp
data=$base/data
# Refuse pre-existing user-controlled paths rather than taking ownership of them.
for directory in "$base" "$base/bin" "$data"; do
  if [[ -L "$directory" ]] || { [[ -e "$directory" ]] &&
    [[ ! -d "$directory" || $(stat -c '%u' "$directory") != 0 ]]; }; then
    echo "Refusing unsafe installation directory: $directory" >&2
    exit 1
  fi
  if [[ -d "$directory" ]]; then
    mode=$(stat -c '%a' "$directory")
    if (( (8#$mode & 8#022) != 0 )); then
      echo "Refusing group/other-writable directory: $directory" >&2
      exit 1
    fi
  fi
done
for config in "$base/config.ini" "$data/config.ini" "$data/brightness-percent" "$data/keyboard-layout"; do
  if [[ -L "$config" ]]; then
    echo "Refusing symlinked config: $config" >&2
    exit 1
  fi
  if [[ -f "$config" ]]; then
    chown root:root "$config"
    chmod 600 "$config"
  fi
done
install -d -o root -g root -m 755 "$base" "$base/bin"
install -d -o root -g root -m 700 "$data"
# User preference: create once, never overwrite on setup/update.
if [[ ! -e "$data/brightness-percent" ]]; then
  printf '1\n' > "$data/brightness-percent"
  chmod 600 "$data/brightness-percent"
fi
for previous in "$base/config.ini" /var/lib/vhp/config.ini; do
  if [[ ! -e "$data/config.ini" && -f "$previous" ]]; then
    install -o root -g root -m 600 "$previous" "$data/config.ini"
    echo "Migrated settings to $data; original retained at $previous."
  fi
done
VHP_DATA_SETUP
sudo install -o root -g root -m 755 "$tmp/vhusbdx86_64" /home/.vhp/bin/vhusbdx86_64
# BEGIN ROOT_CODE_INSTALL
sudo install -o root -g root -m 755 src/vhp-root /home/.vhp/bin/vhp-root
sudo install -o root -g root -m 644 src/touch-stop.py /home/.vhp/bin/touch-stop.py
sudo install -o root -g root -m 644 src/vhp_backend.py src/vhp_hardware.py src/vhp_keyboard.py src/vhp_layouts.json src/vhp_ipc.py /home/.vhp/bin/
# END ROOT_CODE_INSTALL
id -u >"$tmp/owner-uid"
sudo install -o root -g root -m 600 "$tmp/owner-uid" /home/.vhp/bin/owner-uid
sudo install -o root -g root -m 644 "$tmp/build-info.txt" /home/.vhp/bin/build-info.txt
sudo install -o root -g root -m 644 packaging/vhp.service /etc/systemd/system/vhp.service
# Preserve any existing license/settings. Never automatically import checkout files.
# SteamOS's general password-required rule must come before this override.
sudo install -o root -g root -m 440 "$tmp/sudoers" /etc/sudoers.d/zz-vhp
sudo rm -f -- /etc/sudoers.d/vhp
sudo systemctl daemon-reload
sudo visudo -c
# -k ignores cached authentication for this invocation; -n never prompts.
# A real harmless invocation catches rule-order problems that sudo -l misses.
if ! sudo -k -n /home/.vhp/bin/vhp-root check; then
  echo 'ERROR: passwordless VHP access failed. Inspect sudo -l for later overriding rules.' >&2
  exit 1
fi

# BEGIN USER_INSTALL
# No runtime tool should depend on this checkout remaining in place.
install -d -m 755 "$USER_ROOT"
install -m 755 src/vhp.sh src/vhp-gui.sh src/vhp-launch.sh doctor.sh uninstall.sh "$USER_ROOT/"
install -m 644 tools/steam-shortcut.py src/vhp_session.py src/vhp_idle.py src/vhp_preferences.py src/vhp_qt.py src/vhp_ui.py src/vhp_ui.qml src/VhpSettings.qml src/vhp_settings.qml \
  src/vhp_keyboard.py src/vhp_layouts.json src/vhp_ipc.py src/vhp_dashboard.py tools/vhp-gui-deps.py "$USER_ROOT/"
python3 -I "$USER_ROOT/vhp_preferences.py" "$USER_ROOT/launch-mode" "${mode:-gui}"
if [[ -n ${qt_source:-} && $qt_source != "$USER_ROOT/pylib" ]]; then
  # Staging was validated before privileged installation; replacement is rollback-safe.
  qt_stage=$(mktemp -d "$USER_ROOT/.qt-stage.XXXXXX")
  cp -a -- "$qt_source/." "$qt_stage/"
  if [[ -e $USER_ROOT/pylib ]]; then mv -- "$USER_ROOT/pylib" "$qt_stage.previous"; fi
  if ! mv -- "$qt_stage" "$USER_ROOT/pylib"; then
    [[ ! -e $qt_stage.previous ]] || mv -- "$qt_stage.previous" "$USER_ROOT/pylib"
    exit 1
  fi
  rm -rf -- "$qt_stage.previous"
fi
install -d -m 700 "$USER_ROOT/konsole/config" "$USER_ROOT/konsole/data/kxmlgui5/konsole"
install -m 600 packaging/konsole/config/konsolerc "$USER_ROOT/konsole/config/konsolerc"
install -m 600 packaging/konsole/data/kxmlgui5/konsole/*.rc "$USER_ROOT/konsole/data/kxmlgui5/konsole/"
# GUI state is disposable: do not let an older saved toolbar layout override XML.
rm -f -- "$USER_ROOT/konsole/state/konsolestaterc"
# END USER_INSTALL

echo 'VirtualHerePad components installed successfully.'
echo "User tools: $USER_ROOT"
echo 'Settings: /home/.vhp/data/config.ini (created by VirtualHere on first run).'
echo 'Logs: journalctl -u vhp.service'
echo
shortcut_status='skipped (not requested)'
if [[ -t 0 ]]; then
  echo 'If Steam is running, the shortcut helper will offer to shut it down gracefully.'
  if read -r -p 'Add/update the VirtualHerePad Steam shortcut now? [y/N] ' answer; then
    case "$answer" in
      y | Y | yes | YES)
        if python3 "$USER_ROOT/steam-shortcut.py"; then
          shortcut_status='ready (added, updated, or already current)'
        else
          shortcut_status='not updated (see error above)'
          echo 'VirtualHerePad installation succeeded, but the Steam shortcut was not updated.'
          printf 'Follow the message above, then rerun: python3 "%s/steam-shortcut.py"\n' "$USER_ROOT"
        fi
        ;;
      *) printf 'Skipped. Add it later with: python3 "%s/steam-shortcut.py"\n' "$USER_ROOT" ;;
    esac
  fi
else
  shortcut_status='skipped (noninteractive setup)'
  printf 'Noninteractive setup: shortcut skipped. Run python3 "%s/steam-shortcut.py" to add it.\n' "$USER_ROOT"
fi

printf '\n== Setup complete ==\n'
echo "Installation: successful ($commit)"
echo "Steam shortcut: $shortcut_status ($mode interface)"
echo 'Settings: /home/.vhp/data/config.ini (preserved on reinstall)'
echo 'VirtualHerePad is not started or enabled at boot.'
case "$shortcut_status" in
  ready*)
    echo 'Next: in Gaming Mode, open Library > Non-Steam > VirtualHerePad > Play.'
    echo 'A new shortcut may not appear on Home / Recently Played until first launch.'
    echo 'Then connect from the other machine using the VirtualHere client.'
    ;;
  *) printf 'Next: run python3 "%s/steam-shortcut.py", then find VirtualHerePad under Library > Non-Steam.\n' "$USER_ROOT" ;;
esac
printf 'Diagnostics: "%s/doctor.sh"\n' "$USER_ROOT"
printf 'Manual launcher: "%s/vhp-launch.sh" (saved choice: %s)\n' "$USER_ROOT" "$mode"
if [[ $shortcut_status == ready* ]]; then
  echo 'The shortcut uses installed files; the checkout can be moved or deleted.'
else
  echo 'Before deleting the checkout, update any old Steam shortcut to use the installed launcher.'
fi
echo 'Display: defaults to 1%; edit /home/.vhp/data/brightness-percent (0-100) with sudo.'
echo 'Galileo OLED with max 599000 uses measured steps; other models/ranges use generic gamma 2.2.'
echo 'Existing brightness preferences are preserved. VHP checks brightness once per second and corrects external changes.'
echo 'Disable Steam > Settings > Display > Enable Adaptive Brightness to avoid competing adjustments.'
echo 'That setting is yours to change; setup leaves it untouched.'
if [[ $mode != terminal ]]; then
  echo 'Tip: keep the launcher open; hold the HOLD 2s TO QUIT button to stop.'
else
  echo 'Tip: keep the launcher open; hold one finger in any screen corner for 2 seconds to stop.'
  echo 'Fallback: use a local keyboard and Ctrl+C to stop.'
fi
echo 'The Deck Steam button may be forwarded to the VirtualHere client.'
