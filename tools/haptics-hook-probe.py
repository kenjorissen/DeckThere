#!/usr/bin/env python3
"""Temporary, opt-in VirtualHere hook experiment; not installed by setup.sh.

Stop DeckThere, then sudo python3 haptics-hook-probe.py --install-bind to test
controller handoff, or --install for the original client-connect experiment.
Remove with sudo python3 /home/.deckthere/bin/deckthere_haptics_probe.py --remove
while DeckThere is stopped. Configuration/license contents are never logged.
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
BACKUP = CONFIG.with_name("config.ini.before-haptics-probe")
INSTALLED = Path("/home/.deckthere/bin/deckthere_haptics_probe.py")
STOPPING = Path("/run/deckthere/stopping")
PATTERNS = {"connect": (120, 0.150), "bind": (120, 0.150), "disconnect": (350, 0.200)}


def hook_lines(bind=False):
    key, event_name = ("onBind.28de.1205", "bind") if bind else ("onClientConnect", "connect")
    return {
        key.lower().encode(): f"{key}=/usr/bin/timeout 3s /usr/bin/python3 -I {INSTALLED} --event {event_name}".encode(),
        b"onclientdisconnect": f"onClientDisconnect=/usr/bin/timeout 3s /usr/bin/python3 -I {INSTALLED} --event disconnect".encode(),
    }


def configure(data, install, bind=False):
    """Replace only our exact commands, preserving unrelated current settings."""
    known = hook_lines() | hook_lines(True)
    result = []
    for line in data.splitlines(keepends=True):
        key, _, value = line.rstrip(b"\r\n").partition(b"=")
        key = key.strip().lower()
        if install and bind and key in (b"onbind", b"onbind.28de"):
            raise RuntimeError("Existing broader bind hook; refusing to shadow it")
        if key in known:
            if value.strip() != known[key].partition(b"=")[2]:
                raise RuntimeError("Existing or changed hook; refusing to overwrite it")
        else:
            result.append(line)
    data = b"".join(result)
    if install:
        if data and not data.endswith(b"\n"):
            data += b"\n"
        data += b"\n".join(hook_lines(bind).values()) + b"\n"
    return data


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("Expected a regular file")
        data = os.read(fd, 1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise RuntimeError("File exceeds probe size limit")
        return data
    finally:
        os.close(fd)


def atomic_write(path, data, mode):
    fd, name = tempfile.mkstemp(prefix=".haptics-probe-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def install_or_remove(install, bind=False):
    if os.geteuid() != 0:
        raise RuntimeError("Run installation/removal with sudo")
    # Serialize with DeckThere's normal launch/repair operations.
    Path("/run/deckthere-launch").mkdir(mode=0o700, exist_ok=True)
    with open("/run/deckthere-launch/lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = subprocess.run(
            ["/usr/bin/systemctl", "show", "deckthere.service", "-p", "ActiveState", "--value"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        if state not in ("inactive", "failed"):
            raise RuntimeError("Exit DeckThere first; never edit VirtualHere config while running")
        original = read_regular(CONFIG)
        updated = configure(original, install, bind)
        if install:
            # Preserve the first pre-test backup across probe upgrades.
            if BACKUP.exists() or BACKUP.is_symlink():
                read_regular(BACKUP)  # Refuse symlinks/non-regular backup files.
            else:
                with BACKUP.open("xb") as backup:
                    os.fchmod(backup.fileno(), 0o600)
                    backup.write(original)
            atomic_write(INSTALLED, read_regular(Path(__file__).resolve()), 0o644)
        atomic_write(CONFIG, updated, 0o600)
        # Keep the harmless script/backup for diagnostics; remove only our exact hooks.
    print(
        "Probe hooks installed. Launch DeckThere, then connect/disconnect one client."
        if install
        else "Probe hooks removed; all other current settings preserved."
    )


def pulse_report(cycles):
    if cycles not in (120, 350):
        raise ValueError("Only the auditioned patterns are allowed")
    report = bytearray(65)
    # Linux steam_haptic_pulse: both pads, 500 us on/off, finite count, +6 dB.
    report[1:11] = struct.pack("<BBBHHHB", 0x8F, 8, 2, 500, 500, cycles, 6)
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


def event(kind):
    syslog.openlog("deckthere-haptics-probe", syslog.LOG_PID, syslog.LOG_USER)
    started = time.monotonic()
    syslog.syslog(syslog.LOG_INFO, f"{kind}: hook entered")
    if STOPPING.exists():
        syslog.syslog(syslog.LOG_INFO, f"{kind}: skipped during shutdown")
        return
    # No detached work. Client-connect was observed to race USB handoff despite
    # the synchronous callback; this probe tests whether onBind differs.
    # Probe disconnect briefly; do not indefinitely block VH waiting for reattach.
    device = local_controller()
    deadline = started + (1.0 if kind == "disconnect" else 0.1)
    while device is None and time.monotonic() < deadline and not STOPPING.exists():
        time.sleep(0.05)
        device = local_controller()
    if device is None:
        syslog.syslog(syslog.LOG_INFO, f"{kind}: skipped; no locally owned controller")
        return
    node, interface = device
    fd = os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = bytearray(8)
        fcntl.ioctl(fd, 0x80084803, info, True)  # HIDIOCGRAWINFO
        if struct.unpack("=IHH", info) != (3, 0x28DE, 0x1205):
            raise RuntimeError("Device identity changed")
        cycles, gap = PATTERNS[kind]
        for index in range(2):
            if STOPPING.exists() or (interface / "driver").resolve().name != "usbhid":
                syslog.syslog(syslog.LOG_INFO, f"{kind}: cancelled; shutdown or ownership change")
                return
            report = pulse_report(cycles)
            count = fcntl.ioctl(fd, (3 << 30) | (65 << 16) | (ord("H") << 8) | 6, report, True)
            if count != 65:
                raise RuntimeError("Short haptic feature transfer")
            syslog.syslog(
                syslog.LOG_INFO,
                f"{kind}: buzz {index + 1} report sent; elapsed={time.monotonic() - started:.3f}s",
            )
            time.sleep(cycles / 1000 + (gap if index == 0 else 0))
    finally:
        os.close(fd)
    syslog.syslog(
        syslog.LOG_INFO, f"{kind}: two buzz reports sent; elapsed={time.monotonic() - started:.3f}s"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--install-bind", action="store_true")
    group.add_argument("--remove", action="store_true")
    group.add_argument("--event", choices=PATTERNS)
    args = parser.parse_args()
    if args.event:
        # Never deny client access because haptics failed. External timeout bounds
        # even a blocked ioctl; no retries or USB ownership changes are attempted.
        def expired(_signum, _frame):
            raise TimeoutError("Probe deadline")

        signal.signal(signal.SIGALRM, expired)
        signal.alarm(2)
        try:
            event(args.event)
        except Exception as exc:
            syslog.syslog(syslog.LOG_WARNING, f"{args.event}: skipped ({type(exc).__name__})")
        finally:
            signal.alarm(0)
    else:
        install_or_remove(args.install or args.install_bind, args.install_bind)


if __name__ == "__main__":
    main()
