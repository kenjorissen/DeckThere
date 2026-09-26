# VirtualHerePad

Use your Steam Deck as a controller for another computer. VirtualHerePad (VHP)
launches from Steam, lowers the screen brightness, shows a battery/status
dashboard, and inhibits normal sleep while sharing. Choose the **GUI dashboard
with an optional touch keyboard** or the lightweight **terminal dashboard**. Both use one Steam shortcut
and the same VirtualHere service, license, brightness preference, and cleanup.
Hold the GUI's quit button—or a screen corner in terminal mode—to stop
and restore the original brightness.

## What is VirtualHere?

[VirtualHere](https://www.virtualhere.com/) shares **USB devices over a network**.
The receiving computer sees the Deck's controller as if it were plugged into a
local USB port. This is **not game/video streaming**: the game runs on that
computer, and the Deck supplies controller input.

- **Server on the Deck:** VirtualHerePad downloads and runs the VirtualHere USB
  server, manages the launcher, and handles local cleanup.
- **Client on your gaming PC:** you download VirtualHere's client separately to
  connect to the controller. Windows instructions are below; macOS and Linux
  clients are also available.

VirtualHere is proprietary software, separate from VirtualHerePad. See its
[official site](https://www.virtualhere.com/) for licensing, trial limitations,
pricing, and support.

### VirtualHere licensing

**A paid VirtualHere server license is strongly recommended for all users,
including controller-only use.** It supports the software that makes VHP's USB
sharing possible. Purchase and licensing are handled directly by
[VirtualHere](https://www.virtualhere.com/), separately from this free, open-source
project.

> **Author's note:** I'm a big supporter of open-source software, but I fully
> respect companies that build and sell paid software. Until there's an
> open-source solution as clean as VirtualHere, I'll happily pay them for it.

**A license is required for VHP's controller-plus-keyboard support:** the Steam
Controller and VHP Touch Keyboard are **two separate USB devices**, shared at the
same time. The unlicensed one-device allowance is suitable for controller-only
use with either dashboard, not controller-plus-keyboard use.

Keyboard mode also requires selecting **Use** for **both devices** in the
VirtualHere client. VHP neither combines them into one device nor bypasses
VirtualHere's licensing.

## Quick start: Steam Deck

No system packages, pip, virtual environment, or SteamOS read-only changes are
needed. **GUI without a keyboard is the fresh-install default**. GUI setup downloads
a private, matched Qt/PySide6 **6.11.2** runtime (about 76 MiB compressed) when needed;
explicit terminal-only setup skips Qt. GUI needs Python 3.10+ and compatible glibc.
Its volume-button handling uses uinput. The optional virtual USB keyboard needs
stock `dummy_hcd`, `libcomposite`, and `usb_f_hid` support, loaded only when enabled.
Setup checks Qt compatibility; gadget support is checked when the keyboard starts.

1. **Prepare the Deck.** Log into Steam once, switch to **Desktop Mode**, and open
   **Konsole**. Run as your normal user, not root. If you haven't set a sudo
   password yet, run `passwd`.
2. **Clone and install:**

   ```bash
   cd ~
   git clone https://github.com/kenjorissen/VirtualHerePad.git
   cd VirtualHerePad
   ./setup.sh
   ```

3. **Add the shortcut.** Accept setup's offer to add/update **VirtualHerePad**
   in Steam. Fresh installs use the GUI dashboard without a USB keyboard; existing
   installs retain their saved interface and keyboard choice.
   Save games and finish downloads before allowing it to close Steam. Reopen
   Steam when prompted, or open it yourself. If you already have a VirtualHere
   config/license, [import it](#virtualhere-config-and-license) before launching.
4. **Disable adaptive brightness.** In **Gaming Mode**, turn off **Steam >
   Settings > Display > Enable Adaptive Brightness** to avoid repeated brightness
   changes while VHP maintains its selected level. Setup leaves this preference untouched; you can
   re-enable it after sharing.
5. **Find and launch it.** Open **Library > Non-Steam > VirtualHerePad > Play**.
   **Don't look only at Home / Recently Played:** a new shortcut may not appear
   there until its first launch. In Desktop Mode, search the Library for
   `VirtualHerePad` with filters that include non-Steam games.
6. **Connect the gaming PC.** Follow the [Windows client steps](#windows-client-quick-start)
   below and leave the Deck's launcher running.
7. **Stop when finished.** In terminal mode, hold **one finger in any screen
   corner for two seconds**. In either GUI configuration, hold **HOLD 2s TO QUIT**.
   VHP stops sharing and restores brightness.

Steam runs the installed `vhp-launch.sh` without a mode argument under
`~/.local/share/VirtualHerePad`.
You do **not** need to run it separately during setup. Once the shortcut has
been updated, the checkout can be moved or deleted without breaking normal use.

Setup checks VirtualHere's live checksum and reuses the installed server when
it matches; otherwise it downloads and verifies a fresh copy. To supply the file
instead, see [Manual server download](#manual-server-download).

### Selecting or switching interfaces

```bash
./setup.sh                   # keep saved choice; fresh install: GUI without keyboard
./setup.sh --terminal        # terminal; no Qt download
./setup.sh --gui             # GUI; no virtual USB keyboard
./setup.sh --gui --keyboard  # GUI with virtual USB keyboard
```

Setup remembers the full choice; reinstalls keep it unless you select another.
Existing `keyboard` preferences and the legacy `--keyboard` flag mean GUI with
keyboard. `--terminal --keyboard` is invalid. Terminal remains available as a fallback.

Accept shortcut updating **once when upgrading from mode-specific shortcuts**:
the single **VirtualHerePad** entry now reads preferences at launch, preserving
its app ID and artwork. Old explicit `--keyboard`/`--terminal` launch arguments
override the preference until removed. There is no menu at each launch.

**Settings** in the GUI offers keyboard startup for this session, startup plus a
saved keyboard default, and the next-launch choice of GUI, GUI + keyboard, or
Terminal. Saving an interface never interrupts the current sharing session.
The start-and-save action records its default only after successful startup. Enabling it creates
and exports **VHP Touch Keyboard**; select **Use** for that device on the PC.
The ordinary keyboard toggle only shows/hides keys; it does not disable USB.

GUI-only mode displays **KEYBOARD NOT RUNNING**. It does not create/export the
virtual USB keyboard, load its USB gadget modules, or send HID reports. The
status dashboard, volume brightness controls, Settings, and hold-to-quit remain.

Terminal's top-center **SETTINGS** control (or local **S** key) opens the same
settings window when private Qt is installed. It only changes the next launch;
keyboard startup is disabled there. Sharing and heartbeats continue while it is
open. A terminal-only install without Qt instead shows **Settings unavailable —
rerun setup with --gui**. Corner-hold and Ctrl+C remain available regardless.
Switching to terminal from the GUI retains Qt, so Settings remains available.

The popup never runs the installer, downloads packages, prompts for sudo, or
changes Steam shortcuts. Only one sharing session can run at a time. Startup
preferences are kept on normal uninstall; `--purge-settings` removes them.

### Manual server download

Run `./setup.sh --manual-download` to disable downloading. If the file is missing,
setup prints the download URL and required location, then exits without installing.

Download the **generic Linux x86-64** server from the
[VirtualHere server page](https://www.virtualhere.com/usb_server_software) and save
it as **`~/Downloads/vhusbdx86_64`**, owned by your normal user. From the checkout:

```bash
chmod 600 ~/Downloads/vhusbdx86_64
./setup.sh --manual-download
```

Only read permission is needed; do not run the file or give it executable
permission yourself. **Manual mode does not automatically verify VirtualHere's
checksum. Verify the file against the publisher's
[SHA1SUM](https://www.virtualhere.com/sites/default/files/usbserver/SHA1SUM)
before installing.** Setup warns about this; it does not require a hash file.

Setup installs the file at `/home/.vhp/bin/vhusbdx86_64` as **root:root, mode
0755**. Do not copy it directly into the privileged directory. For another local
path, use `VHP_SERVER_PATH="/path/to/vhusbdx86_64" ./setup.sh --manual-download`.
An independently trusted `VHP_SHA256` can also be supplied for an automatic check.

Manual mode makes **no Qt downloads either**. Use `--terminal`, or prepare Qt
separately with `python3 tools/vhp-gui-deps.py` before offline keyboard setup.
`VHP_QT_PATH=/path/to/pylib` can supply an existing matching runtime; setup validates
it before changing the installed service.

## Windows client quick start

1. Open the official [VirtualHere USB Client download page](https://www.virtualhere.com/usb_client_software).
   Choose **Windows x86_64** for a typical Intel/AMD PC, or **Windows ARM64** for
   an ARM-based PC. Save and run the executable; follow any Windows permission
   or driver-installation prompts. Installing it as a service is not required.
2. Connect the PC and Deck to the same trusted local network, and launch
   VirtualHerePad on the Deck.
3. In the client's device tree, right-click **Steam Controller** (the label may
   include **Valve Software**) and select **Use**. Keep the touchscreen local—you
   need it for the keyboard UI or terminal mode's corner-hold exit gesture.
4. In keyboard mode, also select **Use** on **VHP Touch Keyboard**. A licensed
   VirtualHere server is required to share it alongside the controller—these are
   **two devices**, each selected separately in the client. A license is strongly
   recommended for controller-only use too; see [licensing](#virtualhere-licensing).
5. Configure the controller through Steam/Steam Input on the PC as needed, then
   play. Stopping use in the client disconnects the controller; use the Deck's
   quit button (keyboard mode) or corner-hold gesture (terminal mode) to stop VHP
   itself.

If the server doesn't appear, see [Connection troubleshooting](#connection-troubleshooting).

## Daily use

### Touch keyboard and dashboard

The graphical interface opens on a clock/battery/network dashboard. Tap the
**top-center KEYBOARD** button to show/hide the keyboard. A small **Layout: …**
control opens a scrollable/filterable chooser instead of a permanent row of
language buttons. Its selection is remembered across sessions. The layout control
and **HOLD 2s TO QUIT** remain available in both states. The quit button fills while
held; releasing early, sliding off, or losing focus cancels the hold.

- Use the Deck's **Volume Up/Down** buttons to adjust brightness by one step;
  repeat works while held. Other keys on the local AT keyboard are forwarded
  through a replacement input device. If safe discovery/grabbing fails, VHP logs
  a warning and leaves the physical keyboard alone.
- Choose the **exact layout/variant or IME profile** matching your PC. The catalog
  includes regional variants and separate Simplified/Traditional Chinese, Japanese,
  and Korean profiles; familiar names share mappings where appropriate.
  **Selection changes Deck legends, not the PC's settings.** There is no automatic
  layout detection or Unicode injection. IME composition/candidates stay on the PC.
  See [layouts, coverage, limitations and bug reports](docs/keyboard-layouts.md).
- Shift/AltGr can be tapped for the next key; Ctrl/Alt/Super latch until tapped
  again. **RELEASE KEYS**, hiding the keyboard, or losing focus clears local
  held/latched key state. Caps Lock indication tracks VHP taps, not the PC's LED
  state; clearing keys does not toggle Caps Lock.
- Remote typing requires VirtualHere's `usbfs` ownership of the gadget interface.
  Ownership is rechecked at each report write; the check and kernel ownership
  change are not atomic. Do not treat this as a security boundary against a
  deliberately racing local driver.

The root backend is a supervised child of `vhp.service`, not a separately run
terminal command. Closing/crashing the UI ends the backend; the quit button,
backend failure, and heartbeat expiry stop the whole service. Keyboard mode does
not run the corner-hold monitor. `src/vhp-gui-sandbox.sh` is only a compatibility alias
for the installed launcher, not an installer or root-checkout runner.

**Coverage is not a promise of exhaustive testing.** Layout legends are based on
published Windows tables; Linux/macOS mappings and IMEs can differ. I want to
support everyone's layout, but cannot personally test every keyboard, OS, and
input method. **Bug reports, corrections, and successful-configuration reports
are welcome**—please follow the [reporting checklist](docs/keyboard-layouts.md#reporting-a-problem-or-requesting-a-variant)
and do not include passwords or private license/config data.

### Terminal dashboard and shared controls

The colored terminal dashboard shows a block-letter **VirtualHerePad** title,
large battery percentage (green, amber at 30%, red at 15%), charging status,
local clock, primary local IP, connected client IPs, and corner-exit markers.
The large clock sits on the left with battery on the right; server/client status
is below them, above the IP details. The title, clock, and battery scale together:
8-row lettering at 90×30 or larger, 10-row lettering at 120×34 or larger, and
5-row lettering in smaller full layouts. Small terminals get a clipped compact
layout; non-terminal launches use plain text.

- The clock shows local hours/minutes, without seconds or animations.
- Battery data is sampled every **30 seconds** from the system battery.
- Network data is sampled every **5 seconds** using stock `ip` and `ss` tools.
  The local IP is the source selected by a kernel route lookup (IPv4 preferred,
  IPv6 fallback). Lookups do **not** send internet traffic. VPNs can affect the
  selected route/IP.
- **Server running** means the service is active. Client IPs are unique peer
  addresses of established TCP connections to the default server port **7575**.
  A connection does **not** prove the controller is in use. Multiple clients
  behind the same address are grouped; custom server ports are not monitored.
  Long lists are clipped to the terminal width. Network addresses are visible
  on screen, so consider that when sharing screenshots.

The display redraws only when shown values change or the window is resized.
Sampling reuses the heartbeat loop, without persistent extra monitoring processes.
Missing battery/network tools or data show as unavailable; no TCP peers shows
**Waiting for client**. Battery impact has not been measured.

In terminal mode, `vhp-launch.sh --terminal` uses `vhp-gui.sh` to start a separate fullscreen Konsole with
its menu, tabs, scrollbar, and both toolbars hidden. VHP's own configuration and
GUI XML overrides live under `~/.local/share/VirtualHerePad/konsole/`, alongside
isolated state and cache directories. No unsupported toolbar flags are needed.
The normal XDG environment is restored before `vhp.sh` runs. Regular Konsole
windows and manually running `vhp.sh` in Desktop Mode are unaffected. Setup
refreshes these disposable GUI files; uninstall removes them.

When shutdown begins, the dashboard switches to a large **SHUTTING DOWN** message
while VirtualHere exits. Touch/service cleanup is reported through the existing
heartbeat check, normally within about a second; Ctrl+C shows it immediately.
The message remains visible until the stop command finishes, then terminal state
is restored. The brief shutdown wait remains necessary for orderly USB cleanup.

While VHP is running, the service requests systemd inhibition of **sleep** and
**idle**. Both are released on exit. Gaming Mode also needs the normal-user
[idle keepalive](#gaming-mode-idle-handling) described below: the system inhibitor
alone does not prevent Steam from starting a broken suspend transition.
Forced suspension may behave differently. A mostly static image can remain on
screen for the whole session; consider OLED burn-in risk.

For **terminal mode's** corner-hold exit, keep one finger within the outer **12%
of both screen axes** for two seconds. Releasing, moving out, or adding another finger cancels it.
After multiple fingers, lift them all before retrying. A finger already down at
startup must also lift first. All four corners work regardless of rotation.

**Fallbacks:** in terminal mode, use a local Bluetooth keyboard and **Ctrl+C**.
In either mode, use **Steam > Exit Game** if you can reach the local menu. The
Deck's Steam button may be forwarded to the PC instead. From Konsole or SSH, you can also run:

```bash
sudo -n /home/.vhp/bin/vhp-root stop
```

Keep only one launcher open: all launchers control the same service. A touch
request is handled within the existing one-second loop. If Steam force-kills
the launcher, the service stops after about ten seconds without a heartbeat.
A hung USB server gets up to three additional seconds before being killed.

### Gaming Mode idle handling

Both interfaces publish a small activity pulse to Steam's Gamescope X11 root
window every ten seconds, using the existing launcher heartbeat loop. This
starts before the service lowers brightness and ends when the launcher stops
sharing. There is no additional persistent watcher, no keyboard/controller/mouse
input injection, and no change to Steam's saved dim or sleep timeouts. Normal
idle behavior resumes after the pulses stop. Desktop Mode skips this workaround.

This is an **undocumented Gamescope activity-counter workaround**, not a standard
sleep-inhibition API. It addresses two observed interactions:

- Steam can play its sleep animation and leave a black screen when the system
  inhibitor rejects sleep ([upstream report](https://github.com/ValveSoftware/SteamOS/issues/2619)).
- Steam's idle dimming can start its fade from Steam's remembered brightness,
  raising the panel above VHP's selected minimum. Brightness maintenance then
  competes with that fade, causing flicker.

The launcher requires one identifiable local Gamescope session and an existing,
correctly typed activity counter. Missing tools/counter, an ambiguous target,
command failures, or a changed compositor stop startup or end the active VHP
session with an error, rather than silently continuing without this protection.
These checks cannot establish that every future Steam build still honors the
counter. `xprop` and `pgrep` must be available; setup does not install system packages.

For troubleshooting, `VHP_DISABLE_GAMESCOPE_IDLE=1` in the launcher's environment
opts out. **Disable Steam's automatic dimming and sleep manually before using
that opt-out.** Neither option disables adaptive brightness; keep that off to
avoid competing adjustments. VHP never rewrites Steam's power preferences or
restores a stale activity-counter value on exit.

This prevents tested **automatic idle** transitions, not explicit power-button
sleep requests. Exit VHP before requesting sleep: the system inhibitor can still
reject manual sleep, exposing the same Steam bug. Critical-battery settings,
Steam Input, and VirtualHere's controller transport are not changed.

### Screen brightness

The default is **1%** on a nonlinear brightness scale. To change it:

```bash
sudoedit /home/.vhp/data/brightness-percent
```

Put a single whole number from **0 to 100** in the file, without a `%` sign, then
stop and relaunch VHP. Setup creates the file only if it is missing and never
overwrites an existing preference.

VHP selects a curve using the DMI product name and `max_brightness`:

- **Steam Deck OLED (`Galileo`), maximum `599000`:** measured step/percentage
  anchors, with exponential interpolation between adjacent points. **10% sets
  3405.** This is an empirical approximation from one Deck, not Steam's official
  algorithm or a guarantee for every OLED panel/firmware combination.
- **LCD and other models/ranges (including unavailable model identification):**
  generic perceptual approximation `round(max_brightness × (percent / 100)^2.2)`.
  This is not a measured Steam-slider or nits calibration.

| OLED step / percentage | Hardware brightness |
| --- | ---: |
| 0 | 1207 |
| 10 | 3405 |
| 20 | 9604 |
| 30 | 27086 |
| 40 | 76387 |
| 50 | 215423 |
| 60 | 279370 |
| 70 | 362298 |
| 80 | 469843 |
| 90 | 593677 |
| 100 | 593677 |

On the calibrated OLED, `0` means the measured minimum and 90–100 shares the
measured upper plateau, slightly below the hardware maximum. On the generic
curve, `0` writes hardware zero (the screen may go dark) and `100` writes the
hardware maximum. Small values can also round to zero on coarse hardware ranges.

Brightness is set at startup and on keyboard-mode volume-button events. While
running, VHP checks the requested backlight value **about once per second** using
its existing service/backend loops and rewrites it **only if it differs from the
selected level**. No extra watcher process is started.

The selected percentage is the target: volume buttons adjust that percentage
immediately, and the watcher follows the new target even before it is saved.
External brightness changes do not become the target. Button changes are saved
on release and clean shutdown. Terminal mode maintains its startup selection.

Corrections and read/write failures are logged to the service journal, at most
once per 30 seconds. A watch failure does not stop controller sharing. This
actively overrides other brightness controls while VHP is running; disabling
Steam adaptive brightness avoids competing adjustments and visible flicker.
The watcher does not identify which process changed brightness.

Missing or invalid preferences log a warning and fall back to 1%. Parsing is
bounded to six bytes, rejects excess/binary data and non-regular files, and never
executes the contents. A trailing LF or CRLF is accepted.

The watcher stops before the original brightness is restored on normal exit; values are logged
in the journal. If the saved value was already zero, exit restores zero. Dimming
currently uses `amdgpu_bl0` and is skipped if its brightness/maximum is unavailable.
Power loss or forcibly killing the privileged service itself can prevent cleanup.

### VirtualHere config and license

The active config is **`/home/.vhp/data/config.ini`**, created on the first server
run. It is **not** in `~/.vhp`, `/home/deck/.vhp`, or the checkout. The hidden `.vhp`
directory is directly under `/home`; its `data` directory is root-only. Check
that the file exists without displaying private contents:

```bash
sudo ls -l /home/.vhp/data/config.ini
```

To import a config from another VirtualHere installation, run setup first, then:

```bash
sudo -n /home/.vhp/bin/vhp-root stop
sudo install -o root -g root -m 600 /path/to/your/config.ini /home/.vhp/data/config.ini
```

Replace the source path with your actual file. This **replaces** the installed
config; the source is untouched. Launch VirtualHerePad from Steam afterward.
Setup also imports an old `/var/lib/vhp/config.ini` if no current config exists,
leaving the old copy in place.

This file can contain license and connection credentials. Keep it private:
don't commit it or paste it into public bug reports. Keep a separate backup for
factory resets/reimaging; VHP does not automatically back up your server config.

## Update

From your checkout (normally `~/VirtualHerePad`):

```bash
cd ~/VirtualHerePad
git pull
./setup.sh
```

Setup stops the current instance and replaces installed code and user tools.
**Existing config/license, brightness preference, and saved keyboard layout are preserved.** It does
not start VHP or enable it at boot. If you deleted the checkout, clone it again
and run setup.

Accept shortcut updating to point the existing entry at the selected installed
launcher mode. Its app ID and other settings are retained.

Normal SteamOS updates should preserve `/home`. If an update resets the service
or sudo rule in `/etc`, rerun setup to restore integration.

## Remove

Run as your normal user, not with `sudo`, from any directory:

```bash
~/.local/share/VirtualHerePad/uninstall.sh
```

This stops the service and removes installed programs, private Qt runtime, and the sudo rule.
**Settings stay in `/home/.vhp/data`**, including `config.ini` and
`brightness-percent` and `keyboard-layout`; nothing is moved to your user home. Remove the non-Steam
shortcut manually in Steam. The checkout and other old local files are untouched.
The checkout's `./uninstall.sh` also works.

**Only to permanently delete settings/license without a backup:**

```bash
~/.local/share/VirtualHerePad/uninstall.sh --purge-settings
```

This additionally deletes `/home/.vhp` and old `/var/lib/vhp` data.

## Troubleshooting

### Diagnostics and manual launch

```bash
~/.local/share/VirtualHerePad/doctor.sh
systemctl status vhp.service
journalctl -u vhp.service -n 100 --no-pager
```

Use sudo for the journal command if necessary. `doctor.sh` checks tools,
permissions, sudo access, service state, backlight, and logs without changing
settings. It also reports the installed commit, installation time, and binary
hashes. An inactive service is normal when VHP isn't running.

To test the installed launcher directly, run this in the Deck's Konsole:

```bash
~/.local/share/VirtualHerePad/vhp-launch.sh --terminal
# Or: --gui, or --gui --keyboard (if installed with Qt)
```

It starts the same service and adjusts brightness. Review diagnostic logs before
sharing them; the diagnostic script does not read private config contents.

### Connection troubleshooting

Check that VHP is running, both computers can reach each other, and the network
isn't a guest network with client isolation. Check firewall rules too: the server
uses TCP **7575** by default. **Don't expose it to the public internet.** The
VirtualHere client can also connect to a manually specified server address; see
its official documentation.

### Shortcut setup and reference

Read setup's final summary: installation can succeed even if shortcut creation
was skipped or failed. Setup checks prerequisites and account discovery before
downloading. The shortcut helper requires an installed, executable launcher.

To manage the shortcut without a checkout:

```bash
python3 ~/.local/share/VirtualHerePad/steam-shortcut.py
# Check account discovery without changing files or stopping Steam:
python3 ~/.local/share/VirtualHerePad/steam-shortcut.py --check
# Select a userdata ID if the helper lists multiple accounts:
python3 ~/.local/share/VirtualHerePad/steam-shortcut.py --account 12345678
```

The helper backs up `shortcuts.vdf`, preserves other shortcuts, and updates an
existing VirtualHerePad/VHP/vhp.sh entry rather than duplicating it. It asks
before closing Steam, never force-kills it, and refuses to write while Steam is
running or if the file format is unsupported. Noninteractive setup skips these
prompts and never closes/opens Steam automatically.

Manual fields for the normal `deck` account:

| Field | Value |
| --- | --- |
| Name | `VirtualHerePad` |
| Target | `"/usr/bin/env"` |
| Start In | `"/home/deck/.local/share/VirtualHerePad"` |
| Launch Options | `-u LD_PRELOAD "/home/deck/.local/share/VirtualHerePad/vhp-launch.sh"` (reads saved preferences) |
| Steam Overlay | On |
| Force Steam Play compatibility tool | Off (native Linux launcher) |

The scripts use the invoking user's home, not an assumed username or checkout
name. User tools install under `.local/share/VirtualHerePad` in that home, not
`$XDG_DATA_HOME`. Paths with spaces are supported.

## How it works and security

| Location | Purpose |
| --- | --- |
| `~/.local/share/VirtualHerePad` | User-owned launcher, diagnostics, shortcut helper, and uninstaller |
| `/home/.vhp/bin` | Root-owned helper, touch monitor, keyboard backend/modules, installer-selected UID, and VirtualHere binary |
| `/home/.vhp/data` | Private config, brightness preference and saved keyboard layout; directory mode `0700` |
| `/etc/systemd/system/vhp.service` | Manually started service; not enabled at boot |
| `/etc/sudoers.d/zz-vhp` | Fixed passwordless start/start-keyboard/stop/keepalive/check operations |
| `/run/vhp` | Root-owned mode `0711`: traversable, not listable; root-private lease/markers and owner-only GUI socket |
| `/run/vhp-launch` | Root-only mode selection/serialization for the service |

Setup/uninstall do not write to `/usr` or disable SteamOS's read-only protection.
The `/etc` entries use SteamOS's normally writable overlay. The service does not
execute code or read settings from the user-writable checkout or user-tools
folder. Review changes before password-authorizing setup.

The GUI and private Qt runtime run only as the desktop user. The backend uses
isolated system Python (`-I`) and root-owned modules. Its mode-`0600` Unix socket
also checks the peer UID against installer-owned metadata. IPC permits only
bounded keyboard/status operations, not commands or paths. Only the configured
installing user is supported; installing as a different user replaces that owner.
The root backend never uses private Qt or imports Python from the user's home.

Qt wheels are pinned to one matched package version, verified against PyPI's
published SHA-256, checked for Python/glibc compatibility and unsafe archive paths,
then validated in a staged directory before replacement. This trusts PyPI's HTTPS
metadata; the checksum is not an independent signature. Wheel licenses remain
in the private runtime; they are separate from this repository's MIT license.

The touch monitor uses Python's standard library to read direct type-B
multitouch devices non-exclusively, without logging coordinates. It blocks on
input when idle; only a potential hold needs a timer. Missing-device discovery
retries every ten seconds. Touches remain available to other local apps.

A temporary sleep inhibitor replaces persistent system sleep changes. It does
not prevent privileged forced suspension. The sudo rule sorts after SteamOS's
general rule; a harmless probe verifies access without cached authentication.

VirtualHere still runs as root for USB access. Root ownership is not a sandbox
against server vulnerabilities. Use a trusted network; VHP does not configure
firewall rules or server authentication.

By default, setup fetches VirtualHere's current official
[SHA1SUM](https://www.virtualhere.com/sites/default/files/usbserver/SHA1SUM) over
HTTPS first. It checks a private copy of the installed generic x86-64 server
against that live hash and reuses it if it matches, avoiding another binary
download. Otherwise it downloads and verifies a fresh copy. It requires exactly
one matching filename entry and a matching SHA-1 before stopping the running
service or installing files. Missing, malformed,
ambiguous, or mismatched checksums abort installation. SHA-1 is the publisher's
available checksum, not a modern signature; this still trusts VirtualHere's
HTTPS site. [Manual mode](#manual-server-download) explicitly leaves upstream
verification to the user and warns before installation.

`VHP_SHA256=<trusted-sha256> ./setup.sh` adds an independently supplied SHA-256
check; it never bypasses the official check for automatic downloads. It also
works in manual mode. Setup records the verification method and actual SHA-256
for diagnostics. The proprietary binary is not included in Git or GitHub releases.

## Development

### Repository layout

```text
setup.sh, doctor.sh, uninstall.sh   User-facing commands
src/                               Runtime shell/Python, QML, and layout catalog
packaging/                         systemd service and Konsole configuration
tools/                             Installer helpers and layout-data generator
tests/                             Isolated regression tests
docs/                              Detailed feature documentation
```

This is the **source layout**, not the installed layout. Setup copies the runtime
and required helpers into `/home/.vhp/bin` and
`~/.local/share/VirtualHerePad`, keeping their existing installed filenames.
Settings remain in `/home/.vhp/data`; installed launchers, imports and uninstall
work without the checkout. The layout generator is development-only.

Run the commands below from the repository root. Runtime Python modules live in
`src/`; use `PYTHONPATH=src` for ad-hoc imports in development, never to bypass the
installed root backend's isolated Python environment.

There is no build step. On a Linux development machine, have **Python 3, Bash,
GNU make, ShellCheck, and [uv](https://docs.astral.sh/uv/)** available. `uvx` caches
the formatters on first use; no project virtual environment is required. These
are development-only tools, not extra Deck installation requirements.

```bash
make fmt    # Fix Python lint/import issues and format Python + shell files
make lint   # Check Ruff, formatting, ShellCheck, and Bash syntax; no source edits
make test   # All test modules, in up to four isolated processes
make check  # Lint + tests; also the default for plain make
make test-lifecycle  # Focused shutdown/lease/brightness checks
make test-qt         # Qt interface only; requires a working PySide6 runtime
```

Ruff settings are in `ruff.toml`; shfmt uses two-space indentation and indented
case branches. GitHub Actions runs the no-Qt checks and pinned Qt interface tests
as parallel jobs on pushes, pull requests, and manual dispatches, currently with
Python 3.11. The Qt job does not repeat the core suite. Tools and concurrency can
be overridden locally, for example `make check PYTHON=python3.13 TEST_JOBS=2`.
Use `TEST_JOBS=1` for serial debugging. Each `tests/test_NAME.py` also has a focused
`make test-NAME` target; failures from any parallel module fail the overall check.

The Makefile pins Ruff and shfmt-py exactly so formatting is consistent. To
upgrade, change their versions, run `make fmt` and `make check`, and review the
diff. Python, make, and ShellCheck use system versions rather than exact pins.
These development pins do not select the VirtualHere server version; automatic
installation verifies the publisher's current download against its SHA1SUM.

Tests use temporary files and mock services, not USB devices or root access.
They do not install/start the real VHP service. Shortcut tests reject unmocked
input and process launches; stdout/stderr is shown only on failure. For hardware
changes, verify launch, client connection, the selected mode's quit control,
brightness/terminal restoration, and forced-launcher cleanup on the Deck. Qt tests
need a private PySide6 runtime and `QT_QPA_PLATFORM=offscreen`; without it they are
explicitly skipped. With Qt available, run `make check` once in that environment:
it covers both core and GUI tests. If a no-Qt pass already succeeded, add only
`make test-qt` with the Qt runtime available, not a second full suite. CI retains
separate no-Qt coverage. Keep the real lease-expiry and hold-to-quit timing checks;
no test requires a live Deck or a manual idle-observation harness.

## Credits and license

VirtualHerePad began as a local adaptation of
[Deckpad by HelloThisIsFlo](https://github.com/HelloThisIsFlo/Deckpad) and has since
been substantially reworked. Thanks to HelloThisIsFlo and Deckpad's contributors
for the original network-controller workflow, Steam/Konsole launch approach,
dimming and sleep handling, and touchscreen-exit idea.

This repository has its own Git history, not a GitHub fork. Its service
management, installer, shortcut editor, heartbeat cleanup, and standard-library
Python touch monitor were developed for VirtualHerePad. That does not erase its
origins or imply endorsement by Deckpad's authors, who retain their rights.

Repository code/documentation are MIT licensed; see [LICENSE](LICENSE). This
license does not grant rights to Deckpad's code/assets or to the separately
downloaded proprietary VirtualHere server.
