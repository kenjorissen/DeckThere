#!/usr/bin/env python3
"""Non-exclusive Linux type-B touchscreen corner-hold exit for DeckThere.

Uses only the standard library. Run by the root-owned DeckThere service with python -I.
No input coordinates or other user input are logged.
"""

import fcntl
import os
import select
import struct
import time
from pathlib import Path

EV_SYN, EV_ABS = 0, 3
SYN_REPORT, SYN_DROPPED = 0, 3
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 47, 53, 54, 57
INPUT_PROP_DIRECT = 1
EVENT = struct.Struct("@llHHi")  # Linux input_event on the host ABI; signed value.
HOLD_SECONDS = 2.0
CORNER_FRACTION = 0.12
STOP_FILE = Path("/run/deckthere/touch-stop")


def ioctl_read(fd, number, size, initial=b""):
    buffer = bytearray(size)
    buffer[: len(initial)] = initial
    request = (2 << 30) | (size << 16) | (ord("E") << 8) | number
    fcntl.ioctl(fd, request, buffer, True)
    return bytes(buffer)


def abs_info(fd, code):
    return struct.unpack("=6i", ioctl_read(fd, 0x40 + code, 24))


def slot_values(fd, code, count):
    data = ioctl_read(fd, 0x0A, 4 * (count + 1), struct.pack("=i", code))
    return struct.unpack(f"={count + 1}i", data)[1:]


class CornerHold:
    """Evaluate complete multitouch frames, not individual coordinate events."""

    def __init__(self, x_range, y_range, slots):
        self.x_range = x_range
        self.y_range = y_range
        self.slots = slots  # slot -> {id, x, y}; retain positions across liftoff.
        self.blocked = any(slot["id"] >= 0 for slot in slots.values())
        self.candidate = None
        self.since = None

    def cancel(self):
        self.candidate = None
        self.since = None

    def corner(self, slot):
        sides = []
        for coordinate, limits in ((slot["x"], self.x_range), (slot["y"], self.y_range)):
            low, high = limits
            if coordinate is None or high <= low or not low <= coordinate <= high:
                return None
            position = (coordinate - low) / (high - low)
            if position <= CORNER_FRACTION:
                sides.append(0)
            elif position >= 1 - CORNER_FRACTION:
                sides.append(1)
            else:
                return None
        return tuple(sides)

    def frame(self, now):
        active = [(index, slot) for index, slot in self.slots.items() if slot["id"] >= 0]
        if not active:
            self.blocked = False
            self.cancel()
            return
        if self.blocked or len(active) != 1:
            self.blocked = True
            self.cancel()
            return
        index, slot = active[0]
        corner = self.corner(slot)
        if corner is None:
            self.cancel()
            return
        identity = (index, slot["id"], corner)
        if identity != self.candidate:
            self.candidate = identity
            self.since = now

    def expired(self, now):
        return self.since is not None and now - self.since >= HOLD_SECONDS


class Touchscreen:
    def __init__(self, fd):
        self.fd = fd
        props = int.from_bytes(ioctl_read(fd, 0x09, 8), "little")
        axes = int.from_bytes(ioctl_read(fd, 0x20 + EV_ABS, 16), "little")
        required = (ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID)
        if not props & (1 << INPUT_PROP_DIRECT) or not all(axes & (1 << code) for code in required):
            raise ValueError("Not a direct type-B multitouch device")
        slot_info = abs_info(fd, ABS_MT_SLOT)
        if slot_info[1] != 0 or not 0 <= slot_info[2] < 64:
            raise ValueError("Unsupported multitouch slot range")
        self.count = slot_info[2] + 1
        self.x_range = abs_info(fd, ABS_MT_POSITION_X)[1:3]
        self.y_range = abs_info(fd, ABS_MT_POSITION_Y)[1:3]
        if any(high <= low for low, high in (self.x_range, self.y_range)):
            raise ValueError("Invalid touchscreen coordinate range")
        self.dropped = False
        self.pending = False
        self.resync()

    def resync(self):
        # A finger already down when we attach/resync must lift before arming.
        ids = slot_values(self.fd, ABS_MT_TRACKING_ID, self.count)
        xs = slot_values(self.fd, ABS_MT_POSITION_X, self.count)
        ys = slot_values(self.fd, ABS_MT_POSITION_Y, self.count)
        slots = {i: {"id": ids[i], "x": xs[i], "y": ys[i]} for i in range(self.count)}
        self.hold = CornerHold(self.x_range, self.y_range, slots)
        self.slot = abs_info(self.fd, ABS_MT_SLOT)[0]
        self.pending = False

    def feed(self, kind, code, value, now):
        if kind == EV_SYN and code == SYN_DROPPED:
            self.dropped = True
            self.hold.cancel()
            return
        if self.dropped:
            if kind == EV_SYN and code == SYN_REPORT:
                self.resync()
                self.dropped = False
            return
        if kind == EV_ABS:
            self.pending = True
            if code == ABS_MT_SLOT:
                self.slot = value
            elif self.slot in self.hold.slots:
                field = {
                    ABS_MT_TRACKING_ID: "id",
                    ABS_MT_POSITION_X: "x",
                    ABS_MT_POSITION_Y: "y",
                }.get(code)
                if field:
                    self.hold.slots[self.slot][field] = value
            else:
                raise ValueError("Invalid touchscreen slot")
        elif kind == EV_SYN and code == SYN_REPORT:
            self.hold.frame(now)
            self.pending = False

    def drain(self):
        # Drain queued releases/motion before deciding a stationary hold expired.
        while True:
            try:
                data = os.read(self.fd, EVENT.size * 64)
            except BlockingIOError:
                return
            if not data or len(data) % EVENT.size:
                raise OSError("Touchscreen disconnected or malformed input frame")
            for _, _, kind, code, value in EVENT.iter_unpack(data):
                self.feed(kind, code, value, time.monotonic())

    def expired(self):
        return not self.pending and not self.dropped and self.hold.expired(time.monotonic())


def wait_timeout(devices, next_scan, now):
    devices = list(devices)
    if not devices:
        return max(0.0, next_scan - now)
    deadlines = [
        device.hold.since + HOLD_SECONDS
        for device in devices
        if device.hold.since is not None and not device.pending and not device.dropped
    ]
    return max(0.0, min(deadlines) - now) if deadlines else None


def main():
    devices = {}
    next_scan = 0.0
    warned_missing = False
    try:
        while True:
            if not devices and time.monotonic() >= next_scan:
                for path in sorted(Path("/dev/input").glob("event*")):
                    if path in devices:
                        continue
                    fd = None
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                        device = Touchscreen(fd)
                    except (OSError, ValueError):
                        if fd is not None:
                            os.close(fd)
                        continue
                    devices[path] = device
                    warned_missing = False
                    print(
                        f"Touch exit monitoring {path}: hold one finger in any corner for 2 seconds.",
                        flush=True,
                    )
                if not devices and not warned_missing:
                    print(
                        "No supported touchscreen found; retrying. Use local-keyboard Ctrl+C to exit.",
                        flush=True,
                    )
                    warned_missing = True
                next_scan = time.monotonic() + 10
            # No periodic wakeups with an idle connected touchscreen. Only an
            # active gesture needs a deadline; missing-device discovery is slow.
            timeout = wait_timeout(devices.values(), next_scan, time.monotonic())
            select.select([device.fd for device in devices.values()], [], [], timeout)
            for path, device in list(devices.items()):
                try:
                    device.drain()
                    if device.expired():
                        # Root-only runtime directory; helper performs normal cleanup.
                        STOP_FILE.touch(mode=0o600)
                        print(
                            "Touchscreen corner hold detected; requesting DeckThere shutdown.",
                            flush=True,
                        )
                        return
                except (OSError, ValueError) as exc:
                    print(f"Touch exit lost {path}: {exc}; retrying.", flush=True)
                    os.close(device.fd)
                    del devices[path]
    finally:
        for device in devices.values():
            os.close(device.fd)


if __name__ == "__main__":
    main()
