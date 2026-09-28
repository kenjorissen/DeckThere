"""Root-side hardware adapters. Fixed paths only; no GUI-supplied paths/commands."""

import fcntl
import os
import re
import stat
import struct
import subprocess
import tempfile
import time
from pathlib import Path

EVENT = struct.Struct("@llHHi")
EV_SYN, EV_KEY, EV_MSC = 0, 1, 4
VOLUME_DOWN, VOLUME_UP = 114, 115
GADGET = Path("/sys/kernel/config/usb_gadget/deckthere_keyboard")
SERIAL = "deckthere-virtual-keyboard-v1"
# Eight-byte keyboard reports, with the array range extended through LANG2
# (0x91) for ABNT2, JIS and Korean keys. Logical max uses two bytes, not a signed
# one-byte value. The reserved byte, modifier bits and six-key array are unchanged.
REPORT_DESCRIPTOR = bytes.fromhex(
    "05010906a101050719e029e715002501750195088102950175088101"
    "950575010508190129059102950175039101950675081500269100"
    "0507190029918100c0"
)


def ioctl_bytes(fd, number, size):
    data = bytearray(size)
    fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord("E") << 8) | number, data, True)
    return bytes(data)


def bits(data):
    value = int.from_bytes(data, "little")
    return {bit for bit in range(len(data) * 8) if value & (1 << bit)}


def brightness_target(maximum, percent, product):
    if product == "Galileo" and maximum == 599000:
        anchors = (1207, 3405, 9604, 27086, 76387, 215423, 279370, 362298, 469843, 593677, 593677)
        index = min(percent // 10, 9)
        fraction = (percent - index * 10) / 10
        raw = anchors[index] * (anchors[index + 1] / anchors[index]) ** fraction
    else:
        raw = maximum * (percent / 100) ** 2.2
    return min(maximum, max(0, int(raw + 0.5)))


class Brightness:
    def __init__(self):
        self.path = Path("/sys/class/backlight/amdgpu_bl0/brightness")
        self.preference = Path("/home/.deckthere/data/brightness-percent")
        self.stopping = Path("/run/deckthere/stopping")
        self.restore_file = Path("/run/deckthere/brightness-restore")
        self.next_check = 0.0
        self.next_notice = 0.0
        self.percent = self.read_percent()
        try:
            self.maximum = int(self.path.with_name("max_brightness").read_text())
        except (OSError, ValueError):
            self.maximum = 0
        try:
            self.product = Path("/sys/class/dmi/id/product_name").read_text().strip()
        except OSError:
            self.product = ""
        self.original = None
        if self.restore_file.exists():
            self.original = int(self.restore_file.read_text())
            if not 0 <= self.original <= self.maximum:
                raise ValueError("Invalid brightness restoration state")
        self.dirty = False
        if not self.auto_dim:
            self.refresh()

    def read_percent(self):
        try:
            fd = os.open(self.preference, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("Brightness preference is not a regular file")
                data = os.read(fd, 6)
            finally:
                os.close(fd)
            return (
                int(data)
                if re.fullmatch(rb"[0-9]{1,3}(?:\r?\n)?", data) and int(data) <= 100
                else 1
            )
        except OSError:
            return 1

    @property
    def auto_dim(self):
        return self.original is not None

    def current(self):
        value = self.path.read_text().strip()
        if re.fullmatch(r"[0-9]{1,9}", value) is None or not 0 <= int(value) <= self.maximum:
            raise ValueError("Invalid backlight value")
        return int(value)

    def refresh(self):
        """Follow actual brightness only when not maintaining our selected level."""
        try:
            current = self.current()
        except (OSError, ValueError):
            return False
        self.observed_raw = current
        # Keep the selected step across quantized/flat portions of the curve.
        if current == brightness_target(self.maximum, self.percent, self.product):
            return True
        if current >= brightness_target(self.maximum, 100, self.product):
            self.percent = 100
        else:
            self.percent = min(
                range(101),
                key=lambda percent: abs(
                    brightness_target(self.maximum, percent, self.product) - current
                ),
            )
        return True

    def set_auto_dim(self, enabled):
        if self.stopping.exists():
            raise OSError("Session is stopping")
        if enabled == self.auto_dim:
            return
        if self.maximum <= 0:
            raise OSError("Backlight unavailable")
        # Commit only explicit volume adjustments before changing display state.
        self.save()
        if enabled:
            original = self.current()
            percent = self.read_percent()
            target = brightness_target(self.maximum, percent, self.product)
            fd, temporary = tempfile.mkstemp(
                prefix=".brightness-restore-", dir=self.restore_file.parent
            )
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(f"{original}\n")
                os.replace(temporary, self.restore_file)
            finally:
                Path(temporary).unlink(missing_ok=True)
            # Record before touching hardware: service cleanup must be able to
            # restore even if this process dies during the write.
            self.original = original
            self.percent = percent
            self.path.write_text(str(target))
        else:
            self.path.write_text(str(self.original))
            self.restore_file.unlink()
            self.original = None
            self.refresh()
        self.next_check = 0.0

    def change(self, delta):
        if self.maximum <= 0 or self.stopping.exists():
            return
        if not self.auto_dim and not self.refresh():
            return
        percent = min(100, max(0, self.percent + delta))
        target = brightness_target(self.maximum, percent, self.product)
        if not self.auto_dim and (
            (delta > 0 and target < self.observed_raw) or (delta < 0 and target > self.observed_raw)
        ):
            return  # Outside the calibrated range: never adjust the wrong way.
        self.path.write_text(str(target))
        self.percent = percent
        self.pending_percent = percent
        self.dirty = True

    def maintain(self, now=None):
        """Reapply the current selection on drift, at most once per second.

        Volume events and this check run on the same backend thread. With auto-dim
        off this only refreshes the indicator; it never writes. With it on, external
        writes never replace the selected target.
        Return a rate-limited journal notice, not a failure that stops sharing.
        """
        now = time.monotonic() if now is None else now
        if now < self.next_check:
            return None
        self.next_check = now + 1.0
        if self.maximum <= 0 or self.stopping.exists():
            return None
        if not self.auto_dim:
            self.refresh()
            return None
        target = brightness_target(self.maximum, self.percent, self.product)
        try:
            current = self.path.read_text().strip()
            if re.fullmatch(r"[0-9]{1,9}", current) is None:
                raise ValueError("invalid backlight value")
            if int(current) == target:
                return None
            self.path.write_text(str(target))
            notice = f"Backlight changed externally ({current}); reapplied selected {self.percent}% ({target})."
        except (OSError, ValueError) as exc:
            notice = f"WARNING: brightness watcher: {exc}"
        if now >= self.next_notice:
            self.next_notice = now + 30.0
            return notice
        return None

    def save(self):
        if self.dirty:
            fd, temporary = tempfile.mkstemp(prefix=".brightness-", dir=self.preference.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(f"{self.pending_percent}\n")
                os.replace(temporary, self.preference)
                self.dirty = False
            finally:
                Path(temporary).unlink(missing_ok=True)


class VolumeBridge:
    """Grab only the Deck AT keyboard; mirror non-volume keys through uinput.

    No keys are logged. Setup creates the replacement device before grabbing the
    source. Descriptor close releases the grab even if this process is killed.
    """

    def __init__(self, brightness):
        self.source = self.virtual = None
        self.held = set()
        self.brightness = brightness
        self.dropped = False
        self.on_activity = None
        try:
            candidates = []
            for node in Path("/sys/class/input").glob("event*"):
                if (node / "device/name").read_text().strip() == "AT Translated Set 2 keyboard":
                    candidates.append(node.name)
            if len(candidates) != 1:
                raise RuntimeError("Cannot uniquely identify the Deck AT keyboard")
            self.source = os.open("/dev/input/" + candidates[0], os.O_RDONLY | os.O_NONBLOCK)
            identity = struct.unpack("=HHHH", ioctl_bytes(self.source, 0x02, 8))
            if identity[0] != 0x11:  # BUS_I8042; do not grab a USB/Bluetooth keyboard.
                raise RuntimeError("Unexpected AT keyboard bus")
            keys = bits(ioctl_bytes(self.source, 0x21, 96))
            if not {VOLUME_DOWN, VOLUME_UP} <= keys:
                raise RuntimeError("AT keyboard lacks volume keys")
            if bits(ioctl_bytes(self.source, 0x18, 96)):
                raise RuntimeError(
                    "Release all local keys and restart before enabling brightness buttons"
                )
            self.virtual = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(self.virtual, 0x40045564, EV_KEY)
            for key in keys - {VOLUME_DOWN, VOLUME_UP}:
                fcntl.ioctl(self.virtual, 0x40045565, key)
            msc = bits(ioctl_bytes(self.source, 0x24, 8))
            if msc:
                fcntl.ioctl(self.virtual, 0x40045564, EV_MSC)
                for code in msc:
                    fcntl.ioctl(self.virtual, 0x40045568, code)
            setup = struct.pack("=HHHH80sI", 6, 0, 0, 1, b"DeckThere Local Keys", 0)
            fcntl.ioctl(self.virtual, 0x405C5503, setup)  # UI_DEV_SETUP
            fcntl.ioctl(self.virtual, 0x5501)  # UI_DEV_CREATE
            name = bytearray(128)
            fcntl.ioctl(self.virtual, 0x8080552C, name, True)  # UI_GET_SYSNAME
            node = Path("/sys/devices/virtual/input") / bytes(name).split(b"\0", 1)[0].decode()
            end = time.monotonic() + 2
            while not list(node.glob("event*")):
                if time.monotonic() >= end:
                    raise RuntimeError("Replacement keyboard did not appear")
                time.sleep(0.02)
            fcntl.ioctl(self.source, 0x40044590, 1)  # EVIOCGRAB
            if bits(ioctl_bytes(self.source, 0x18, 96)):
                raise RuntimeError("A local key was pressed during setup; release keys and restart")
        except Exception:
            self.close()
            raise

    def emit(self, kind, code, value):
        if os.write(self.virtual, EVENT.pack(0, 0, kind, code, value)) != EVENT.size:
            raise OSError("Short uinput write")

    def release(self):
        for code in self.held:
            self.emit(EV_KEY, code, 0)
        self.held.clear()
        self.emit(EV_SYN, 0, 0)

    def process(self):
        try:
            data = os.read(self.source, EVENT.size * 64)
        except BlockingIOError:
            return
        if not data:
            raise OSError("AT keyboard disconnected")
        for _, _, kind, code, value in EVENT.iter_unpack(data):
            if self.on_activity is not None and (kind == EV_KEY or (kind == EV_SYN and code == 3)):
                self.on_activity()
            if kind == EV_SYN and code == 3:  # SYN_DROPPED
                self.release()
                self.dropped = True
                continue
            if self.dropped:
                if kind == EV_SYN and code == 0:
                    self.dropped = False
                    # Do not invent presses after overflow; release state is safe.
                continue
            if kind == EV_KEY and code in (VOLUME_DOWN, VOLUME_UP):
                if value in (1, 2):
                    self.brightness.change(1 if code == VOLUME_UP else -1)
                elif value == 0:
                    self.brightness.save()
                continue
            if kind == EV_KEY:
                if value == 1:
                    self.held.add(code)
                elif value == 0:
                    self.held.discard(code)
            if kind in (EV_SYN, EV_KEY, EV_MSC):
                self.emit(kind, code, value)

    def close(self):
        if self.virtual is not None:
            try:
                self.release()
                fcntl.ioctl(self.virtual, 0x5502)  # UI_DEV_DESTROY
            except OSError:
                pass
            os.close(self.virtual)
            self.virtual = None
        if self.source is not None:
            os.close(self.source)
            self.source = None
        self.brightness.save()


class Gadget:
    def __init__(self):
        self.fd = None
        self.owned = False
        for module in ("dummy_hcd", "libcomposite", "usb_f_hid"):
            subprocess.run(["/usr/bin/modprobe", module], check=True, timeout=5)
        if not os.path.ismount("/sys/kernel/config"):
            subprocess.run(
                ["/usr/bin/mount", "-t", "configfs", "none", "/sys/kernel/config"],
                check=True,
                timeout=5,
            )
        if GADGET.exists():
            raise RuntimeError(
                "Existing DeckThere gadget found; stop DeckThere and run backend cleanup first"
            )
        try:
            GADGET.mkdir()
            self.owned = True
            for path, value in {
                "idVendor": "0x1d6b",
                "idProduct": "0x0104",
                "bcdUSB": "0x0200",
            }.items():
                (GADGET / path).write_text(value)
            (GADGET / "strings/0x409").mkdir()
            for name, value in {
                "manufacturer": "DeckThere",
                "product": "DeckThere Touch Keyboard",
                "serialnumber": SERIAL,
            }.items():
                (GADGET / "strings/0x409" / name).write_text(value)
            (GADGET / "configs/c.1").mkdir()
            (GADGET / "configs/c.1/MaxPower").write_text("100")
            function = GADGET / "functions/hid.usb0"
            function.mkdir()
            for name, value in {"protocol": "1", "subclass": "1", "report_length": "8"}.items():
                (function / name).write_text(value)
            (function / "report_desc").write_bytes(REPORT_DESCRIPTOR)
            (GADGET / "configs/c.1/hid.usb0").symlink_to(function)
            (GADGET / "UDC").write_text("dummy_udc.0")
            # Match the configfs function's own device number rather than assuming
            # hidg0. /dev/char/ is a udev convenience, so fall back to matching
            # the device number of any hidg node.
            major, minor = map(int, (function / "dev").read_text().strip().split(":"))
            wanted = os.makedev(major, minor)
            node = Path(f"/dev/char/{major}:{minor}")
            end = time.monotonic() + 2
            while True:
                if node.exists():
                    break
                matches = [
                    candidate
                    for candidate in Path("/dev").glob("hidg*")
                    if candidate.stat().st_rdev == wanted
                ]
                if matches:
                    node = matches[0]
                    break
                if time.monotonic() >= end:
                    raise RuntimeError("HID device node did not appear")
                time.sleep(0.02)
            self.fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        except Exception:
            self.close()
            raise

    def shared(self):
        # Never type on the Deck: require usbfs ownership, not the local HID driver.
        for device in Path("/sys/bus/usb/devices").glob("*"):
            try:
                if (device / "serial").read_text().strip() != SERIAL:
                    continue
                interfaces = list(device.glob(device.name + ":*"))
                return bool(interfaces) and all(
                    (item / "driver").resolve().name == "usbfs" for item in interfaces
                )
            except OSError:
                continue
        return False

    def close(self):
        # Unbind before closing the report endpoint: a final report must never
        # be sent to a local driver that reclaimed the interface after unsharing.
        if self.owned:
            try:
                (GADGET / "UDC").write_text("\n")
            except OSError:
                pass
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.owned:
            cleanup_gadget()
            self.owned = False


def cleanup_gadget():
    if not GADGET.exists():
        return
    # Only the fixed DeckThere namespace; never touch another gadget or unload modules.
    try:
        (GADGET / "UDC").write_text("\n")
    except OSError:
        pass
    link = GADGET / "configs/c.1/hid.usb0"
    if link.is_symlink():
        link.unlink()
    for relative in ("functions/hid.usb0", "configs/c.1", "strings/0x409"):
        path = GADGET / relative
        if path.exists():
            path.rmdir()
    GADGET.rmdir()


if __name__ == "__main__":
    # Recovery after a service crash/kill. Never remove an unrelated gadget.
    if GADGET.exists():
        identity = GADGET / "strings/0x409/serialnumber"
        if not identity.exists() or identity.read_text().strip() != SERIAL:
            raise SystemExit("Refusing cleanup: DeckThere gadget identity does not match")
        cleanup_gadget()
