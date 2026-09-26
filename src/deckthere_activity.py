#!/usr/bin/env python3
"""Privileged, passive activity snapshots. Never owns USB interfaces or suspends.

The owner-only socket is read-only: connecting requests a snapshot and extends a
three-second observation lease. With no requests, no input descriptors are open.
Only aggregate times leave this process; input reports are never logged.
"""

import ctypes
import fcntl
import json
import os
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deckthere_hardware import EVENT, ioctl_bytes  # noqa: E402

SOCKET = Path("/run/deckthere/activity.sock")
VOLUME_ACTIVITY = Path("/run/deckthere/volume-activity")
GETX = (1 << 30) | (24 << 16) | (0x92 << 8) | 10
STATS = (2 << 30) | (8 << 16) | (0x92 << 8) | 3
BUTTON_MASK = int.from_bytes(bytes([255, 255, 0x47, 4, 0, 6, 4, 0]), "little")
TOUCH_MASK = (0x18 << 16) | (0xC0 << 40)
STICK_DEADZONE = 2048
TRIGGER_DEADZONE = 256


def report_active(data):
    """Decode the known Deck report only. Gyro and frame counters are ignored."""
    if len(data) != 64 or data[:3] != b"\x01\x00\x09" or data[3] not in (56, 64):
        raise ValueError("Unsupported controller report")
    buttons = int.from_bytes(data[8:16], "little")
    return bool(
        buttons & (BUTTON_MASK | TOUCH_MASK)
        or any(abs(value) > STICK_DEADZONE for value in struct.unpack_from("<4h", data, 48))
        or any(value > TRIGGER_DEADZONE for value in struct.unpack_from("<2H", data, 44))
    )


def controller():
    candidates = []
    for device in Path("/sys/bus/usb/devices").glob("*"):
        try:
            if (device / "idVendor").read_text().strip() == "28de" and (
                device / "idProduct"
            ).read_text().strip() == "1205":
                interfaces = list(device.parent.glob(device.name + ":*"))
                if interfaces and all(
                    (item / "driver").resolve().name == "usbfs" for item in interfaces
                ):
                    candidates.append(
                        (
                            device.name,
                            int((device / "busnum").read_text()),
                            int((device / "devnum").read_text()),
                        )
                    )
        except OSError:
            continue
    if len(candidates) != 1:
        raise OSError("Shared controller unavailable or ambiguous")
    return candidates[0]


def volume_activity():
    """The grabbed volume bridge records only an aggregate monotonic timestamp."""
    try:
        with VOLUME_ACTIVITY.open() as stream:
            return float(stream.read(40))
    except (OSError, ValueError):
        return 0.0


class LocalInputs:
    """Nonexclusive touchscreen and AT keyboard readers, with live held-state queries."""

    def __init__(self):
        self.devices = []
        try:
            for node in sorted(Path("/sys/class/input").glob("event*")):
                fd = os.open("/dev/input/" + node.name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                keep = False
                try:
                    name = (node / "device/name").read_text().strip()
                    props = int.from_bytes(ioctl_bytes(fd, 0x09, 8), "little")
                    axes = int.from_bytes(ioctl_bytes(fd, 0x20 + 3, 16), "little")
                    if props & 2 and axes & (1 << 47) and axes & (1 << 57):  # Direct type-B touch
                        info = struct.unpack("=6i", ioctl_bytes(fd, 0x40 + 47, 24))
                        if info[1] != 0 or not 0 <= info[2] < 64:
                            raise ValueError("Unsupported touchscreen slots")
                        self.devices.append((fd, info[2] + 1))
                        keep = True
                    elif name == "AT Translated Set 2 keyboard":
                        self.devices.append((fd, 0))
                        keep = True
                finally:
                    if not keep:
                        os.close(fd)
            if not any(count for _, count in self.devices) or not any(
                count == 0 for _, count in self.devices
            ):
                raise OSError("Local activity sources unavailable")
        except Exception:
            self.close()
            raise

    def poll(self):
        active = False
        for fd, count in self.devices:
            for _ in range(16):
                try:
                    data = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    break
                if not data or len(data) % EVENT.size:
                    raise OSError("Lost local input")
                for _, _, kind, code, _ in EVENT.iter_unpack(data):
                    if kind == 0 and code == 3:
                        raise OSError("Dropped local input")
                    active |= kind in (1, 2, 3)
            else:
                raise OSError("Local input backlog")
            if count:
                size = 4 * (count + 1)
                values = bytearray(struct.pack("=i", 57) + bytes(size - 4))
                fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord("E") << 8) | 0x0A, values, True)
                active |= any(value >= 0 for value in struct.unpack(f"={count + 1}i", values)[1:])
            else:
                # EVIOCGKEY works even while the brightness bridge grabs events.
                active |= any(ioctl_bytes(fd, 0x18, 96))
        return active

    def close(self):
        for fd, _ in self.devices:
            os.close(fd)
        self.devices.clear()


class Observer:
    def __init__(self):
        self.fd = None
        self.local = None
        self.loaded_here = False
        self.identity = controller()
        self.activity = time.monotonic()
        self.last_report = 0.0
        self.was_active = False
        self.next_identity = 0.0
        try:
            self.local = LocalInputs()
            self.loaded_here = not Path("/sys/module/usbmon").exists()
            if self.loaded_here:
                subprocess.run(
                    ["/usr/bin/modprobe", "usbmon"], check=True, timeout=1, capture_output=True
                )
            node = Path(f"/dev/usbmon{self.identity[1]}")
            # A missing udev node is a recoverable unavailable state, not a reason
            # to create devices or broaden permissions.
            info = node.lstat()
            if not stat.S_ISCHR(info.st_mode) or info.st_uid != 0 or info.st_mode & 4:
                raise OSError("Unsafe USB monitoring device")
            self.fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.header = ctypes.create_string_buffer(64)
            self.payload = ctypes.create_string_buffer(64)
            self.args = struct.pack(
                "=QQQ", ctypes.addressof(self.header), ctypes.addressof(self.payload), 64
            )
        except Exception:
            self.close()
            raise

    def poll(self, now):
        if now >= self.next_identity:
            if controller() != self.identity:
                raise OSError("Controller identity changed")
            self.next_identity = now + 1
        for _ in range(2048):
            try:
                fcntl.ioctl(self.fd, GETX, self.args)
            except BlockingIOError:
                break
            h = self.header.raw
            # Only the observed native interrupt-IN endpoint, never unrelated
            # devices, HID keyboards, output/control traffic or submit events.
            if (h[8], h[9], h[10], h[11], struct.unpack_from("=H", h, 12)[0]) != (
                ord("C"),
                1,
                0x83,
                self.identity[2],
                self.identity[1],
            ):
                continue
            status, length, captured = struct.unpack_from("=iII", h, 28)
            if status or h[15] or length != 64 or captured < 64:
                raise OSError("Incomplete controller report")
            active = report_active(self.payload.raw)
            if not self.last_report or now - self.last_report >= 1:
                self.activity = now  # A recovered stream starts a fresh idle interval.
            if active or self.was_active:
                self.activity = now
            self.was_active = active
            self.last_report = now
        else:
            raise OSError("USB monitoring backlog")
        stats = bytearray(8)
        fcntl.ioctl(self.fd, STATS, stats, True)
        if struct.unpack("=II", stats)[1]:
            raise OSError("Dropped USB monitoring events")
        if self.local.poll():
            self.activity = now
        stamp = volume_activity()
        if self.activity < stamp <= now:
            self.activity = stamp
        return {"healthy": 0 <= now - self.last_report < 1, "activity": self.activity}

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.local is not None:
            self.local.close()
            self.local = None
        if self.loaded_here:
            self.loaded_here = False
            try:
                subprocess.run(
                    ["/usr/bin/modprobe", "-r", "usbmon"],
                    capture_output=True,
                    timeout=1,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass  # Never force removal if another consumer needs the module.


def serve(owner, path=SOCKET):
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    observer = None
    deadline = retry = 0.0
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = False
    try:
        old = os.umask(0o177)
        try:
            server.bind(str(path))  # Never replace an existing socket/symlink.
            bound = True
        finally:
            os.umask(old)
        os.chown(path, owner, -1)
        server.listen(4)
        server.setblocking(False)
        while running:
            ready = select.select([server], [], [], 0.05 if observer is not None else 0.2)[0]
            if not running:
                break
            # Drain input after waiting, before replying, rather than returning
            # a cached pre-wait sample at the countdown boundary.
            now = time.monotonic()
            state = {"healthy": False, "activity": now}
            if now >= deadline and observer is not None:
                observer.close()
                observer = None
            if now < deadline:
                try:
                    if observer is None and now >= retry:
                        observer = Observer()
                    if observer is not None:
                        state = observer.poll(time.monotonic())
                except (OSError, ValueError, subprocess.SubprocessError):
                    if observer is not None:
                        observer.close()
                        observer = None
                    retry = time.monotonic() + 3
            if ready:
                connection, _ = server.accept()
                with connection:
                    _, uid, _ = struct.unpack(
                        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    )
                    if uid != owner:
                        continue
                    deadline = time.monotonic() + 3
                    connection.settimeout(0.1)
                    try:
                        connection.sendall(json.dumps(state).encode() + b"\n")
                    except OSError:
                        pass
    finally:
        if observer is not None:
            observer.close()
        server.close()
        if bound:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Installed activity observer requires root")
    serve(int(Path("/home/.deckthere/bin/owner-uid").read_text()))
