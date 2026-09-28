# Technical reference

For installation and everyday controls, start with the [README](../README.md).

- [Downloads and verification](#downloads-and-verification)
- [Installed paths and privileges](#installed-paths-and-privileges)
- [Launch repair](#launch-repair)
- [Session lifecycle](#session-lifecycle)
- [Idle protection](#idle-protection)
- [Optional automatic sleep](#optional-automatic-sleep)
- [Brightness calibration](#brightness-calibration)
- [Dashboard reporting](#dashboard-reporting)
- [Manual Steam shortcut](#manual-steam-shortcut)

## Downloads and verification

### Automatic installation

Setup fetches VirtualHere's official
[SHA1SUM](https://www.virtualhere.com/sites/default/files/usbserver/SHA1SUM) over
HTTPS. It checks a private copy of the installed generic Linux x86-64 server
against that live hash and reuses it if it matches. Otherwise it downloads and
verifies a fresh copy before stopping the service or installing files.

The manifest must contain exactly one matching filename. Missing, malformed,
ambiguous, or mismatched checksums abort installation. SHA-1 is the publisher's
available checksum, not a modern signature; verification still trusts the
publisher's HTTPS site.

An independently trusted SHA-256 can add a second check:

```bash
DECKTHERE_SHA256="<trusted-sha256>" ./setup.sh
```

It does not bypass the official check for automatic downloads. Setup records the
verification method and actual binary hashes for diagnostics. The proprietary
server is not bundled in Git or GitHub releases.

### Manual server download

Download the **generic Linux x86-64** server from the
[VirtualHere server page](https://www.virtualhere.com/usb_server_software), then:

```bash
chmod 600 ~/Downloads/vhusbdx86_64
./setup.sh --manual-download
```

Only read permission is needed; do not run the file or copy it into the privileged
installation directory yourself. Setup installs it as root-owned executable code.
If the file is missing, setup prints the expected path and download URL, then exits.
For another source path:

```bash
DECKTHERE_SERVER_PATH=/path/to/vhusbdx86_64 ./setup.sh --manual-download
```

**Manual mode does not fetch the official checksum.** Verify the file yourself
against the publisher's SHA1SUM before installation. `DECKTHERE_SHA256` also works
in manual mode if you have an independently trusted hash. Setup warns when
upstream verification is left to you.

Manual mode makes **no Qt downloads either**. Use `--terminal`, retain an installed
matching runtime, or prepare Qt separately while online:

```bash
python3 tools/deckthere-gui-deps.py
```

`DECKTHERE_QT_PATH=/path/to/pylib` selects another prepared runtime. Setup checks
it before changing the service. Qt packages use the [pinned version](../README.md#install-on-the-deck) and are
verified against PyPI's published SHA-256, and checked for Python/glibc compatibility and
unsafe archive paths. Staged validation precedes replacement. This trusts PyPI's
HTTPS metadata, not an independent signature. Package licenses remain in `pylib`.

## Installed paths and privileges

| Location | Purpose |
| --- | --- |
| `~/.local/share/deckthere` | User-owned launcher, GUI/private Qt, artwork, diagnostics, shortcut helper, and uninstaller |
| `~/.local/share/deckthere/launch-mode` | Saved startup choice: `gui`, `keyboard` (GUI + keyboard), or `terminal` |
| `~/.local/share/deckthere/auto-dim` | Saved launch dimming (`0` off, `1` on; missing/invalid defaults on); normal uninstall preserves it |
| `~/.local/share/deckthere/sleep-minutes` | Saved idle timeout (`0`, `5`, `15`, `30`, `60`); normal uninstall retains it, purge removes it |
| `/home/.deckthere/bin` | Root-owned helper, backend/modules, touch monitor, installer-selected UID, and VirtualHere binary |
| `/home/.deckthere/data` | Private config, brightness preference, and keyboard layout; directory mode `0700` |
| `/etc/systemd/system/deckthere.service` | Sharing service; not enabled at boot |
| `/etc/sudoers.d/zz-deckthere` | Fixed `start`, `start-gui`, `start-keyboard`, `stop`, `keepalive`, and `check` helper actions |
| `/run/deckthere` | Root-owned mode `0711`; private lease/markers and owner-only GUI/activity sockets |
| `/run/deckthere-launch` | Root-only service mode selection and start serialization |

User paths use the invoking user's home, not `$XDG_DATA_HOME` or the checkout.
Paths with spaces are supported. Setup/uninstall do not write to `/usr` or disable
SteamOS's read-only protection; `/etc` integration uses its writable overlay.

The GUI and private Qt run as the desktop user. The privileged backend uses system
Python with isolation (`-I`) and root-owned modules, never the checkout or private
Qt. Its Unix socket has mode `0600` and checks the peer UID against installer-owned
metadata. Messages allow bounded keyboard/status operations and a boolean session
auto-dim toggle, not supplied shell commands or paths. Installing as another user replaces the configured owner;
concurrent multi-user operation is not supported.

VirtualHere still runs as root for USB access. Root ownership is **not a sandbox**
against server vulnerabilities. Review code before authorizing setup and use a
trusted network.

## Launch repair

The installed `deckthere-launch.sh` checks integration before either interface,
Gamescope pulses, or service startup. A healthy launch is silent. The fixed
`check` action verifies the saved unit/rule, safe installed-code ownership, and
systemd's loaded unit; `sudo -k -n` does not use cached authentication or prompt.

When repair is needed, a normal-user `kdialog` offers Repair/Cancel. Authorization
uses `pkexec --disable-internal-agent`, never a DeckThere password field or a root
GUI. The launcher reuses an existing same-user KDE authentication agent or starts
SteamOS's stock agent temporarily for this interaction. It keeps the actual Steam
launch/display context and stops only the agent it started, including on cancel
or launcher termination. A healthy launch never starts an agent. There is no
passwordless repair action, extra polkit policy, or persistent agent installation.
Without working native graphical authentication, repair fails closed with Desktop
Mode instructions; it does not fall back to a hidden terminal password prompt.
Confirmation and authentication have bounded timeouts. The noninteractive check
must pass again before sharing can start.

Setup retains root-owned `deckthere.service`, `deckthere.sudoers`, and the fixed
`deckthere_repair.py` under `/home/.deckthere/bin`. Repair takes the same root lock
as service startup, refuses active/transitional sharing, and restores only the
unit and sudo rule from those trusted copies. It validates sudo syntax, publishes
files atomically, reloads systemd and verifies the result; failures roll back the
integration files changed by that attempt. Once authorized, a bounded publication/
rollback transaction defers INT/TERM until it finishes; power loss or SIGKILL
cannot be made transactional across both files.

Repair does not download/update executables, stop/start services, edit shortcuts,
change settings/license, enable boot startup, or relax SteamOS protection. Unsafe
paths, missing code, masked/custom units or drop-ins require manual attention.
Conflicting system sudo rules may also require manual correction; DeckThere does
not edit other policies or retry authorization repeatedly. Repair requires intact,
trusted installed helpers and templates; if these are missing, rerun setup.
Uninstall removes the repair files with the code.

## Session lifecycle

The normal-user launcher owns the session lock and refreshes a root-private lease.
If the launcher is killed, sharing stops after about ten seconds without a
heartbeat. The root helper stops children before restoring raw brightness if the
current session has auto-dim enabled; a hung USB server gets up to three seconds
before forced termination. The systemd unit uses control-group cleanup with a
15-second stop timeout. See [brightness state](#brightness-state) for restoration
and live-toggle details.

In GUI mode the root backend owns brightness, the volume-key bridge, and any
virtual keyboard. A missing/unsafe volume-key source produces a warning instead
of preventing sharing. Non-volume keys from the grabbed AT keyboard are forwarded
through a replacement local input device. Closing the UI or losing its backend
connection ends the installed session.

The virtual keyboard is created only when enabled. **Stop keyboard** in Settings
removes that gadget while VirtualHere and the brightness/volume adapters keep
running. Shared keyboard modules are not unloaded. Typing requires `usbfs` ownership, checked
before each HID write; that check and a kernel ownership change are not atomic.
It is not a security boundary against a deliberately racing local driver.

Terminal mode uses a separate Konsole with configuration/state/cache under
`~/.local/share/deckthere/konsole`. The normal XDG environment is restored before
the sharing launcher runs. Its corner monitor reads direct type-B touchscreen
events non-exclusively, does not log coordinates, and leaves touches available to
other local apps. It blocks while idle and retries missing-device discovery every
ten seconds. The terminal shows **SHUTTING DOWN** during cleanup and restores its
TTY state afterward.

## Idle protection

A systemd block inhibitor covers sleep and idle while sharing. Gaming Mode also
needs normal-user Gamescope activity pulses: the system inhibitor alone does not
prevent Steam from beginning a rejected sleep transition. Steam idle dimming can
also fade from a brighter remembered level and compete with brightness maintenance.

Pulses update an existing, typed activity counter on a uniquely identified
same-user Gamescope compositor. They use the launcher's existing loop, not a new
persistent process. They inject no keyboard/controller/mouse input, change no
saved power settings, and never restore a stale counter value. `xprop` and `pgrep`
must be available; setup does not install system packages for them.

For troubleshooting, set `DECKTHERE_DISABLE_GAMESCOPE_IDLE=1` in the launcher's
environment. **Manually disable Steam's automatic dimming and sleep before using
this opt-out.** This protection is independent of DeckThere's auto-dim choice and
[adaptive brightness](#adaptive-brightness-warning).

Normal idle behavior resumes when pulses stop; future Steam builds may interpret
the undocumented counter differently. Critical-battery settings, Steam Input, and
VirtualHere's controller transport are not changed. See the
[manual-sleep warning](../README.md#gaming-mode-idle-handling).

## Optional automatic sleep

The normal-user supervisor implements the saved inactivity policy in its existing
GUI/terminal loop. A service-owned observer provides aggregate activity timestamps
over `/run/deckthere/activity.sock` (mode `0600`, installer UID checked). This is a
read-only snapshot interface, not a command or suspend endpoint. With **Never**, the observer does not load modules or open
input devices. When enabled, snapshot requests maintain a three-second observation
lease; expiry closes descriptors and attempts to unload `usbmon` only if this
observer loaded it. Modules in use by another consumer are never forcibly removed.

Observation uses the [stock usbmon binary ABI](https://docs.kernel.org/usb/usbmon.html)
and the Steam Deck controller
`28de:1205` native interrupt-IN endpoint `0x83`, while all interfaces remain owned
by `usbfs`. It never claims, detaches or injects into the shared controller. Unknown
identity/report formats, dropped events, backlogs, lost local input or a controller
report gap of a second prevent idle expiry. Decoder support is deliberately narrow;
unsupported hardware remains awake. USB monitoring exposes bus-wide data to the
root process; unrelated device payloads are discarded. Raw input and key codes are
not written to logs or exported to the user process.

Buttons, touch flags and held sticks/triggers count continuously. Raw stick and
trigger deadzones are 4096 and 256 respectively; frame counters and gyro/IMU values
are ignored. Report fields follow [Linux hid-steam](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-steam.c).
Direct type-B touchscreen and AT keyboard observation is nonexclusive,
including held-state queries. The grabbed GUI volume bridge records aggregate
activity time, including taps at brightness limits. Qt and terminal interactions
also cancel pending sleep without changing the saved timeout.

User-owned `sleep-state`, `sleep-activity` and `sleep-warning-seen` files are bounded,
private, atomically replaced session data. Each launch initializes a new timer;
these files are removed on uninstall. A fresh warning-display acknowledgement is
required throughout the 30-second countdown. A delayed supervisor, stale warning,
invalid preference or unavailable detector fails awake. The existing Gamescope
protection and service inhibitor stay active until shutdown.

At expiry the launcher closes its UI, stops its owned service and waits for cleanup.
Only an inactive service with a successful result permits the normal-user
`systemctl --no-ask-password suspend` request. Cancellation during cleanup or a
failed stop prevents it. The request is consumed before execution, never retried
on refusal or wake, and never bypasses another inhibitor. Actual suspend remains
subject to the session's normal system policy. Sharing does not resume on wake.

## Brightness calibration

DeckThere uses `/sys/class/backlight/amdgpu_bl0`; dimming is skipped if the
brightness/maximum cannot be read. The dim-level preference accepts an integer 0–100,
optionally followed by LF or CRLF. Reads are bounded, reject non-regular files,
and never execute the contents.

- **OLED (`Galileo`) with maximum `599000`:** empirical anchors from one Deck,
  with exponential interpolation. This is not Steam's official algorithm or a
  guarantee for every OLED panel/firmware combination.
- **Other models/ranges, including unavailable model identification:**
  `round(max_brightness × (percent / 100)^2.2)`. This is a generic perceptual curve,
  not a measured Steam-slider or nits calibration.

| OLED percentage | Raw brightness |
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

The OLED curve uses its measured minimum at zero and an upper plateau at 90–100.
The generic curve writes hardware zero at zero percent and hardware maximum at
100%; small values may also round to zero on coarse ranges.

While auto-dim is on, maintenance uses existing loops, at most once per second,
and writes only on a mismatch. The current selected percentage remains the target
even before it is saved. Corrections and maintenance read/write failures are
logged at most once per 30 seconds; these failures are nonfatal to sharing.

### Brightness state

The saved launch preference is read by a root-installed helper subprocess that
drops supplementary groups, GID and UID to the installed owner before opening
user-owned data. It never imports or executes code from the user's home as root.

A root-private `/run/deckthere/brightness-restore` marker records the pre-dim raw
level before applying the dim target. The GUI backend updates this marker for live
toggles. Shutdown reads it only after stopping the backend, so restoration follows
the current session state, not the saved launch preference.

With auto-dim off, the GUI reads the physical level for its indicator and rebases
manual volume adjustments on the calibrated curve without enforcing a target.
Quantized/flat steps retain the selected percentage; out-of-range values never
make a manual adjustment move in the wrong direction. Only requested volume
adjustments are saved, even if another brightness controller changes the level
before the key is released. See [user controls](../README.md#screen-brightness)
for toggle and restoration behavior.

### Adaptive-brightness warning

The GUI checks Steam's saved adaptive-brightness override as the normal user during
its five-second dashboard sample. Reads are bounded and reject special files and
final symlinks. A single explicit `0` hides the subdued warning; enabled, missing,
ambiguous or unreadable values show it. Steam can omit the enabled override, and
its saved file can lag live state. This is a conservative hint, not a supported
Steam API or proof of live adaptive state. The reader does not log other Steam
configuration values or modify the file. Backlight writes do not synchronize
Steam's slider or adaptive target, and DeckThere does not identify which process
changed brightness.

## Dashboard reporting

Battery data is sampled every 30 seconds; network data every five seconds. The
clock shows local hours/minutes. Sampling reuses existing loops and does not run
an extra persistent monitoring process. Battery impact has not been measured.

The local IP comes from a kernel route lookup (IPv4 preferred, IPv6 fallback),
which sends no internet traffic. VPNs can affect that result. Client addresses
come from established TCP connections to the default server port **7575**, using
stock `ip` and `ss` tools. Addresses are deduplicated; custom server ports are not
monitored. A TCP connection is not proof of USB device ownership. Missing data
shows as unavailable, and terminal text is clipped to the available width. The
GUI's compact client label uses the same TCP data, not a VirtualHere device-status
API. See [GUI controls](../README.md#gui-and-touch-keyboard) for its presentation.

## Manual Steam shortcut

The installed shortcut helper is recommended. For manual entry under the normal
`deck` account:

| Field | Value |
| --- | --- |
| Name | `DeckThere` |
| Target | `"/usr/bin/env"` |
| Start In | `"/home/deck/.local/share/deckthere"` |
| Launch Options | `-u LD_PRELOAD "/home/deck/.local/share/deckthere/deckthere-launch.sh"` |
| Steam Overlay | On |
| Force Steam Play compatibility tool | Off (native Linux launcher) |

Adjust the home path for another username. Omit interface flags so the shortcut
follows the saved startup choice. Installed artwork lives in the user's
`deckthere/artwork` directory. The helper fills missing library images under the
selected Steam account's `config/grid`, using the shortcut's stored app ID rather
than replacing it or overwriting custom images.
