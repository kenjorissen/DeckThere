#!/usr/bin/env python3
"""Best-effort connection ticks, without USB claims or raw controller writes.

Only a locally available Deck evdev rumble device is eligible. VirtualHere may
remove it while sharing; notifications are then skipped, not queued for later.
"""

import fcntl
import os
import signal
import struct
import time
from pathlib import Path

EVENT = struct.Struct("@llHHi")
EV_FF = 0x15
FF_RUMBLE = 0x50
LENGTH_MS = 35
GAP = 0.14  # Time between the two disconnect ticks.


def ioctl_code(direction, number, size):
    return (direction << 30) | (size << 16) | (ord("E") << 8) | number


def ioctl_bytes(fd, number, size):
    data = bytearray(size)
    fcntl.ioctl(fd, ioctl_code(2, number, size), data, True)
    return bytes(data)


def has_bit(data, bit):
    return bool(int.from_bytes(data, "little") & (1 << bit))


def virtualhere_clients(root=Path("/proc/net")):
    """Return presence, or None for an unknown sample; never log addresses."""
    present = False
    for name in ("tcp", "tcp6"):
        try:
            lines = (root / name).read_text().splitlines()
        except FileNotFoundError:
            if name == "tcp6":  # IPv6 may be disabled.
                continue
            return None
        except OSError:
            return None
        if not lines or "local_address" not in lines[0]:
            return None
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 4:
                return None
            try:
                port = int(fields[1].rsplit(":", 1)[1], 16)
                state = int(fields[3], 16)
            except (ValueError, IndexError):
                return None
            if port == 7575 and state == 1:  # TCP_ESTABLISHED
                present = True
    return present


class Connections:
    """First/last-client edges after one second of stable observations."""

    def __init__(self):
        self.connected = False
        self.candidate = None
        self.since = 0.0

    def update(self, present, now):
        if present is None or present == self.connected:
            self.candidate = None
            return 0
        if present != self.candidate:
            self.candidate = present
            self.since = now
        elif now - self.since >= 1.0:
            self.connected = present
            self.candidate = None
            return 1 if present else 2
        return 0


class Haptics:
    """Own only our short-lived FF effect; no grabbing, gain changes or HID writes."""

    def __init__(self, root=Path("/dev/input")):
        self.root = root
        self.fd = None
        self.effect_id = None
        self.remaining = 0
        self.due = 0.0

    def start(self, count, now):
        self.close()
        # DeckThere is x86-64; ff_effect has size 48 and union offset 16 there.
        if struct.calcsize("P") != 8 or count not in (1, 2):
            return
        for path in sorted(self.root.glob("event*")):
            fd = None
            try:
                fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
                bus, vendor, product, _ = struct.unpack("=HHHH", ioctl_bytes(fd, 0x02, 8))
                if (bus, vendor, product) != (3, 0x28DE, 0x1205):
                    continue
                # Reject Steam's virtual pads and unrelated force-feedback devices.
                if not ioctl_bytes(fd, 0x07, 256).startswith(b"usb-"):
                    continue
                if not has_bit(ioctl_bytes(fd, 0x20, 4), EV_FF):
                    continue
                if not has_bit(ioctl_bytes(fd, 0x20 + EV_FF, 16), FF_RUMBLE):
                    continue
                effect = bytearray(48)
                struct.pack_into("=Hh", effect, 0, FF_RUMBLE, -1)
                struct.pack_into("=HH", effect, 10, LENGTH_MS, 0)
                struct.pack_into("=HH", effect, 16, 0x5000, 0x5000)
                fcntl.ioctl(fd, ioctl_code(1, 0x80, 48), effect, True)
                effect_id = struct.unpack_from("=h", effect, 2)[0]
                if effect_id < 0:
                    continue
                self.fd, fd = fd, None
                self.effect_id = effect_id
                self.remaining = count
                self.due = now
                self.advance(now)
                return
            except OSError:
                pass  # Unsupported/busy/disappearing device: never affect sharing.
            finally:
                if fd is not None:
                    os.close(fd)

    def advance(self, now):
        if self.fd is None or now < self.due:
            return
        if not self.remaining:
            self.close()
            return
        try:
            data = EVENT.pack(0, 0, EV_FF, self.effect_id, 1)
            if os.write(self.fd, data) != len(data):
                raise OSError("Short force-feedback write")
            self.remaining -= 1
            self.due = now + (GAP if self.remaining else LENGTH_MS / 1000 + 0.05)
        except OSError:
            self.close()

    def close(self):
        if self.fd is not None:
            try:
                # Closing evdev erases this descriptor's effects, not other users'.
                os.close(self.fd)
            except OSError:
                pass
        self.fd = self.effect_id = None
        self.remaining = 0


def monitor():
    haptics = Haptics()
    connections = Connections()
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_sample = 0.0
    try:
        while not stopping and not Path("/run/deckthere/stopping").exists():
            now = time.monotonic()
            if now >= next_sample:
                next_sample = now + 0.5
                count = connections.update(virtualhere_clients(), now)
                if count and not stopping:
                    haptics.start(count, now)
            if stopping or Path("/run/deckthere/stopping").exists():
                break
            haptics.advance(now)
            time.sleep(0.02 if haptics.fd is not None else 0.1)
    finally:
        haptics.close()


if __name__ == "__main__":
    monitor()
