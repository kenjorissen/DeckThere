#!/usr/bin/env python3
"""Bounded VirtualHere haptic hooks. No USB claims, grabs or persistent HID settings.

Default: two short buzzes on bind, two longer on client disconnect. Optional
fanfare/power-down notes use the same bounded pulse path. Playback requires local
ownership. This is not a TCP presence poller or a rumble daemon.
"""

import argparse
import fcntl
import os
import signal
import stat
import struct
import subprocess
import syslog
import tempfile
import time
from pathlib import Path

CONFIG = Path("/home/.deckthere/data/config.ini")
INSTALLED = Path("/home/.deckthere/bin/deckthere_haptics.py")
PROBE = INSTALLED.with_name("deckthere_haptics_probe.py")
STOPPING = Path("/run/deckthere/stopping")
PATTERNS = {"bind": (120, 0.150), "disconnect": (350, 0.200)}
# Gain, not perceived loudness percentages. Strong matches the hardware audition.
GAINS = {"quiet": -12, "normal": -3, "strong": 6}
PATTERN_NAMES = ("buzzes", "fanfare")
# Pitch (Hz), note length (ms). Short Charge-style fanfare and descending power-down.
# These are pulse trains, not speaker samples or the Deck's proprietary startup sound.
FANFARE = {
    "bind": ((392, 80), (523, 80), (659, 80), (784, 140), (659, 80), (784, 260)),
    "disconnect": ((784, 100), (659, 100), (523, 120), (392, 150), (262, 250)),
}


def hook_lines():
    return {
        b"onbind.28de.1205": f"onBind.28de.1205=/usr/bin/timeout 3s /usr/bin/python3 -I {INSTALLED} --event bind".encode(),
        b"onclientdisconnect": f"onClientDisconnect=/usr/bin/timeout 3s /usr/bin/python3 -I {INSTALLED} --event disconnect".encode(),
    }


def configure(data, remove=False):
    """Migrate only our probe/production commands; do not shadow custom hooks."""
    hooks = hook_lines()
    known = {key: {line.partition(b"=")[2]} for key, line in hooks.items()}
    for key, event in (
        (b"onclientconnect", "connect"),
        (b"onbind.28de.1205", "bind"),
        (b"onclientdisconnect", "disconnect"),
    ):
        known.setdefault(key, set()).add(
            f"/usr/bin/timeout 3s /usr/bin/python3 -I {PROBE} --event {event}".encode()
        )
    result = []
    for line in data.splitlines(keepends=True):
        key, _, value = line.rstrip(b"\r\n").partition(b"=")
        key = key.strip().lower()
        if not remove and key in (b"onbind", b"onbind.28de"):
            raise ValueError("Custom bind hook present; automatic haptic hooks not installed")
        if key in known and value.strip() in known[key]:
            continue
        if not remove and key in known:
            raise ValueError(
                "Custom client/device hook present; automatic haptic hooks not installed"
            )
        result.append(line)
    data = b"".join(result)
    if remove:
        return data
    if data and not data.endswith(b"\n"):
        data += b"\n"
    return data + b"\n".join(hooks.values()) + b"\n"


def read_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Configuration is not a regular file")
        data = os.read(fd, 1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("Configuration exceeds size limit")
        return data
    finally:
        os.close(fd)


def prepare(path=CONFIG, remove=False):
    """Called only by the service before starting VirtualHere, never while live."""
    try:
        original = read_config(path)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError("Refusing configuration symlink") from None
        original = b""
    updated = configure(original, remove)
    if updated == original:
        return
    backup = path.with_name("config.ini.before-haptic-hooks")
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass  # Never replace the first backup, or read its potentially private contents.
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
    fd, temporary = tempfile.mkstemp(prefix=".haptic-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def preference():
    """Read user files only in an isolated child after dropping root privileges."""
    try:
        value = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(INSTALLED.with_name("deckthere_preferences.py")),
                "--installed-haptics",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=0.5,
        ).stdout.strip()
        fields = value.split()
        if len(fields) == 1 and fields[0] in GAINS:
            return value  # Older installed preference helper: original buzzes.
        if len(fields) == 2 and fields[0] in GAINS and fields[1] in PATTERN_NAMES:
            return value
        return "off"
    except (OSError, subprocess.SubprocessError):
        return "off"  # Fail quiet, never delay sharing for preference problems.


def pattern_steps(kind, pattern):
    """Return bounded (half-period µs, repetitions, following silence seconds)."""
    if pattern == "buzzes":
        cycles, gap = PATTERNS[kind]
        return ((500, cycles, gap), (500, cycles, 0))
    if pattern != "fanfare":
        raise ValueError("Unknown haptic pattern")
    notes = FANFARE[kind]
    result = []
    for index, (frequency, duration_ms) in enumerate(notes):
        half = round(500_000 / frequency)
        cycles = max(1, round(duration_ms * 1000 / (2 * half)))
        result.append((half, cycles, 0.02 if index < len(notes) - 1 else 0))
    return tuple(result)


def pulse_report(cycles, strength, half_period=500):
    if not 1 <= cycles <= 350 or not 250 <= half_period <= 2500 or strength not in GAINS:
        raise ValueError("Unsupported haptic pattern")
    report = bytearray(65)
    # Linux steam_haptic_pulse: both pads, bounded on/off times/count, signed dB gain.
    report[1:11] = struct.pack(
        "<BBBHHHb", 0x8F, 8, 2, half_period, half_period, cycles, GAINS[strength]
    )
    return report


def local_controller():
    candidates = []
    for entry in Path("/sys/class/hidraw").glob("hidraw*"):
        try:
            device = (entry / "device").resolve()
            props = dict(
                line.split("=", 1)
                for line in (device / "uevent").read_text().splitlines()
                if "=" in line
            )
            if props.get("HID_ID") != "0003:000028DE:00001205":
                continue
            if not props.get("HID_PHYS", "").endswith("/input2"):
                continue
            interface = next(p for p in device.parents if (p / "bInterfaceNumber").exists())
            if (interface / "driver").resolve().name == "usbhid":
                candidates.append((Path("/dev") / entry.name, interface))
        except (OSError, StopIteration):
            continue
    return candidates[0] if len(candidates) == 1 else None


def event(kind, strength=None, pattern="buzzes"):
    if STOPPING.exists():
        return False
    # User-mode previews supply only a whitelisted gain, never paths or raw reports.
    if strength is None:
        selection = preference().split()
        strength = selection[0] if selection else "off"
        pattern = selection[1] if len(selection) == 2 else "buzzes"
    if strength not in GAINS or pattern not in PATTERN_NAMES:
        return False
    started = time.monotonic()
    device = local_controller()
    deadline = started + (0.5 if kind == "disconnect" else 0.1)
    while device is None and time.monotonic() < deadline and not STOPPING.exists():
        time.sleep(0.05)
        device = local_controller()
    if device is None:
        syslog.syslog(syslog.LOG_INFO, f"{kind}: haptics skipped; controller not local")
        return False
    node, interface = device
    fd = os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = bytearray(8)
        fcntl.ioctl(fd, 0x80084803, info, True)
        if struct.unpack("=IHH", info) != (3, 0x28DE, 0x1205):
            raise ValueError("Controller identity changed")
        for half_period, cycles, gap in pattern_steps(kind, pattern):
            if STOPPING.exists() or (interface / "driver").resolve().name != "usbhid":
                syslog.syslog(
                    syslog.LOG_INFO, f"{kind}: haptics cancelled; shutdown or ownership change"
                )
                return False
            report = pulse_report(cycles, strength, half_period)
            count = fcntl.ioctl(fd, (3 << 30) | (65 << 16) | (ord("H") << 8) | 6, report, True)
            if count != 65:
                raise OSError("Short haptic feature transfer")
            time.sleep(cycles * 2 * half_period / 1_000_000 + gap)
    finally:
        os.close(fd)
    syslog.syslog(syslog.LOG_INFO, f"{kind}: haptic pattern sent ({pattern}, {strength})")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--remove-hooks", action="store_true")
    group.add_argument("--event", choices=PATTERNS)
    group.add_argument("--preview", choices=GAINS, help="unprivileged connect-pattern preview")
    parser.add_argument(
        "--pattern", choices=PATTERN_NAMES, help="preview pattern (default: buzzes)"
    )
    args = parser.parse_args()
    if args.pattern is not None and args.preview is None:
        parser.error("--pattern requires --preview")
    syslog.openlog("deckthere-haptics", syslog.LOG_PID, syslog.LOG_USER)
    if os.geteuid() != 0 and args.preview is None:
        parser.error("Installed haptic hooks require root")
    if args.prepare or args.remove_hooks:
        try:
            prepare(remove=args.remove_hooks)
        except (OSError, ValueError) as exc:
            if args.remove_hooks:
                # Do not let uninstall delete code while its callbacks remain configured.
                raise SystemExit("Could not remove haptic hooks; installed code retained") from None
            # Keep VirtualHere's existing config and sharing usable; no contents logged.
            print(
                f"WARNING: haptic hook configuration unchanged ({type(exc).__name__}); check for custom hooks.",
                flush=True,
            )
        return

    def expired(_signum, _frame):
        raise TimeoutError("Haptic deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(2)
    played = False
    kind = "bind" if args.preview is not None else args.event
    try:
        played = event(kind, args.preview, args.pattern or "buzzes")
    except Exception as exc:
        syslog.syslog(syslog.LOG_WARNING, f"{kind}: haptics skipped ({type(exc).__name__})")
    finally:
        signal.alarm(0)
    if args.preview is not None and not played:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
