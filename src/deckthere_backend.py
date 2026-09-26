#!/usr/bin/env python3
"""Root-side backend for the DeckThere touch keyboard and brightness buttons.

Runs as root. Owns the USB gadget, the volume-key grab, and brightness. The Qt
UI is a normal user process and talks to this over a Unix socket; it can only
send a known operation name, an HID key code, and a boolean.

Installed mode is supervised by deckthere.service and reads its owner from root-owned
installation metadata. The manual command-line interface is for development;
it is never granted through sudoers.
"""

import argparse
import grp
import os
import select
import signal
import socket
import stat
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deckthere_ipc  # noqa: E402
from deckthere_hardware import Brightness, Gadget, VolumeBridge  # noqa: E402
from deckthere_keyboard import KeyState  # noqa: E402

STATUS_INTERVAL = 2.0  # Only while a UI client is connected.


def read_layout(path, default="us"):
    """Bounded data-only preference; never follow symlinks or block on a FIFO."""
    if path is None:
        return default
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return default
            data = os.read(fd, 65)
        finally:
            os.close(fd)
        value = data.decode("ascii").strip()
        return value if len(data) <= 64 and value in deckthere_ipc.LAYOUTS else default
    except (OSError, UnicodeError):
        return default


def save_layout(path, layout):
    if layout not in deckthere_ipc.LAYOUTS:
        raise ValueError("Unknown layout")
    fd, temporary = tempfile.mkstemp(prefix=".keyboard-layout-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(layout + "\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Hardware:
    """Bundle of privileged adapters, injectable so the backend is testable."""

    def __init__(self, gadget, brightness, volume):
        self.gadget = gadget
        self.brightness = brightness
        self.volume = volume


class DisabledKeyboard:
    """No gadget construction, sysfs probing, module loading or HID endpoint."""

    fd = None

    def shared(self):
        return False

    def close(self):
        pass


def default_hardware(options):
    gadget = DisabledKeyboard() if getattr(options, "no_keyboard", False) else Gadget()
    try:
        brightness = Brightness()
        volume = None
        if not options.no_volume_keys:
            try:
                volume = VolumeBridge(brightness)
                if getattr(options, "installed", False):
                    # A second evdev reader cannot see grabbed key events. Export
                    # only activity time, including volume presses at brightness limits.
                    volume.on_activity = lambda: Path("/run/deckthere/volume-activity").write_text(
                        str(time.monotonic())
                    )
            except (OSError, RuntimeError) as exc:
                # A missing local keyboard must not make controller sharing unusable.
                print(f"WARNING: brightness buttons unavailable: {exc}", file=sys.stderr)
    except Exception:
        gadget.close()
        raise
    return Hardware(gadget, brightness, volume)


class Backend:
    def __init__(self, options, hardware=None):
        self.options = options
        self.hardware = hardware if hardware is not None else default_hardware(options)
        self.gadget = self.hardware.gadget
        self.brightness = self.hardware.brightness
        self.volume = self.hardware.volume
        self.keys = KeyState()
        self.layout_store = getattr(options, "layout_store", None)
        self.layout = read_layout(self.layout_store, options.layout)
        self.stopping = False
        self.shared = False
        self.connection = None
        self.next_status = 0.0
        self.reader = deckthere_ipc.Reader()
        self.reports = deque()
        self.bound = False
        self.installed = bool(getattr(options, "installed", False))
        self.connect_deadline = time.monotonic() + 15
        self.quiet = bool(getattr(options, "quiet", False))

    def notice(self, message):
        """Print unless the caller asked for silence (tests, embedded use)."""
        if not self.quiet:
            print(message, flush=True)

    # -- reporting ---------------------------------------------------------
    def status(self):
        return {
            "op": "status",
            "shared": self.shared,
            "keyboard": not isinstance(self.gadget, DisabledKeyboard),
            "stopping": Path("/run/deckthere/stopping").exists(),
            "percent": self.brightness.percent,
            "layout": self.layout,
            "keys": len(self.keys.keys),
        }

    def send(self, message):
        if self.connection is None:
            return
        try:
            self.connection.sendall(deckthere_ipc.encode(message))
        except OSError:
            self.disconnect()

    # -- protocol ----------------------------------------------------------
    def handle(self, message):
        op = message["op"]
        if op == "ping":
            self.send({"op": "pong"})
        elif op == "status":
            self.send(self.status())
        elif op == "keyboard_start":
            if isinstance(self.gadget, DisabledKeyboard):
                try:
                    gadget = Gadget()
                except Exception as exc:
                    self.notice(f"WARNING: virtual keyboard could not start: {exc}")
                    self.send({"op": "keyboard_error"})
                    return
                self.gadget = self.hardware.gadget = gadget
            self.shared = self.gadget.shared()
            self.send(self.status())
        elif op == "keyboard_stop":
            self.reports.clear()
            self.keys.clear()
            try:
                # Unbind/remove only DeckThere's keyboard, not the server or volume adapter.
                self.gadget.close()
            except Exception as exc:
                # Retain the adapter so a partial cleanup can be retried.
                self.queue_report(self.keys.clear())
                self.notice(f"WARNING: virtual keyboard could not stop: {exc}")
                self.send({"op": "keyboard_error"})
                return
            self.gadget = self.hardware.gadget = DisabledKeyboard()
            self.shared = False
            self.send(self.status())
        elif op == "key":
            self.press(message["code"], message["down"])
        elif op == "clear":
            self.queue_report(self.keys.clear())
        elif op == "layout":
            self.reports.clear()
            self.queue_report(self.keys.clear())
            self.layout = message["layout"]
            if self.layout_store is not None:
                try:
                    save_layout(self.layout_store, self.layout)
                except OSError as exc:
                    self.notice(f"WARNING: could not save keyboard layout: {exc}")
            self.send(self.status())
        elif op == "stop":
            self.stopping = True

    def press(self, code, down):
        # Typing is only meaningful once the PC has bound the keyboard, and it
        # must never be possible for a report to land on the Deck's own desktop.
        if self.gadget.fd is None or not self.gadget.shared():
            self.keys.clear()
            self.reports.clear()
            return
        try:
            report = self.keys.update(code, down)
        except ValueError:
            return  # Ignore, never crash: a bad press must not drop the session.
        self.queue_report(report)

    def queue_report(self, report):
        if self.gadget.fd is None or not self.gadget.shared():
            self.keys.clear()
            self.reports.clear()
            return
        if len(self.reports) >= 128:
            raise OSError("HID output queue overflow; stopping to release the device")
        self.reports.append(report)

    def flush_report(self):
        # Check ownership at each write, not merely on the periodic status tick.
        # Unsharing can still race a sysfs check; never queue reports across it.
        if not self.gadget.shared():
            self.reports.clear()
            self.keys.clear()
            return
        try:
            count = os.write(self.gadget.fd, self.reports[0])
        except BlockingIOError:
            return
        if count != 8:
            raise OSError("Short HID report write")
        self.reports.popleft()

    # -- connection --------------------------------------------------------
    def accept(self, server):
        connection, _ = server.accept()
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, gid = (
            int.from_bytes(credentials[index : index + 4], "little") for index in (0, 4, 8)
        )
        del pid, gid
        if uid != self.options.owner_uid:
            # Never explain to a rejected peer, and never log its identity.
            connection.close()
            return None
        connection.setblocking(False)
        self.connection = connection
        self.reader = deckthere_ipc.Reader()
        self.shared = self.gadget.shared()
        self.send(self.status())
        # Re-arm the periodic report so a fresh client is not answered twice.
        self.next_status = time.monotonic() + STATUS_INTERVAL
        return connection

    def disconnect(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None
        self.reader = deckthere_ipc.Reader()
        self.reports.clear()
        report = self.keys.clear()
        if self.gadget.fd is not None and self.gadget.shared():
            try:
                os.write(self.gadget.fd, report)
            except OSError:
                pass  # Installed mode tears down USB as the final release guarantee.
        if self.installed:
            self.stopping = True

    # -- main loop ---------------------------------------------------------
    def run(self):
        server = None
        try:
            server = self.bind()
            while not self.stopping:
                watches = [server]
                if self.connection is not None:
                    watches.append(self.connection)
                if self.volume is not None:
                    watches.append(self.volume.source)
                writes = [self.gadget.fd] if self.reports else []
                ready, writable, _ = select.select(watches, writes, [], 0.5)
                if writable:
                    self.flush_report()
                for item in ready:
                    if item is server:
                        if self.connection is None:
                            self.accept(server)
                        else:
                            item.accept()[0].close()  # One UI at a time.
                    elif self.volume is not None and item == self.volume.source:
                        previous_percent = self.brightness.percent
                        self.volume.process()
                        if self.brightness.percent != previous_percent:
                            # Publish once after this event batch, not on the next
                            # periodic tick. Unchanged/clamped keys stay quiet.
                            self.next_status = 0.0
                    elif self.connection is not None:
                        self.read(self.reader)
                if not self.stopping:
                    notice = self.brightness.maintain()
                    if notice:
                        self.notice(notice)
                if (
                    self.installed
                    and self.connection is None
                    and time.monotonic() > self.connect_deadline
                ):
                    raise TimeoutError("UI did not connect within 15 seconds")
                if self.connection is not None and time.monotonic() >= self.next_status:
                    self.next_status = time.monotonic() + STATUS_INTERVAL
                    shared = self.gadget.shared()
                    if shared != self.shared:
                        self.shared = shared
                        if not shared:
                            self.keys.clear()
                            self.reports.clear()
                    self.send(self.status())
        finally:
            self.close(server)

    def read(self, reader):
        try:
            data = self.connection.recv(4096)
        except OSError:
            self.disconnect()
            return
        if not data:
            self.disconnect()
            return
        try:
            lines = reader.feed(data)
            for line in lines:
                if self.connection is None or self.stopping:
                    break
                self.handle(deckthere_ipc.decode(line))
        except deckthere_ipc.ProtocolError:
            # A malformed or oversized frame is a hard error: drop that client.
            self.disconnect()

    def bind(self):
        path = self.options.socket
        if path.exists() or path.is_symlink():
            raise SystemExit(f"Refusing existing socket path: {path}")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            old_umask = os.umask(0o177)
            try:
                server.bind(str(path))
                self.bound = True
            finally:
                os.umask(old_umask)
            os.chown(path, self.options.owner_uid, self.options.owner_gid)
            os.chmod(path, 0o600)
            server.listen(1)
            server.setblocking(False)
        except OSError:
            # Never leave a stale socket that a later run would have to trust.
            server.close()
            if self.bound:
                path.unlink(missing_ok=True)
                self.bound = False
            raise
        # Announced only once the socket is actually accepting connections.
        self.notice(f"backend ready: {path} (owner uid {self.options.owner_uid})")
        return server

    def close(self, server):
        self.disconnect()
        if server is not None:
            server.close()
        if self.bound:
            try:
                self.options.socket.unlink(missing_ok=True)
            except OSError as exc:
                self.notice(f"WARNING: removing backend socket: {exc}")
            self.bound = False
        # One failing adapter must not leave another adapter grabbed/bound.
        for action in (
            self.volume.close if self.volume is not None else lambda: None,
            self.gadget.close,
            self.brightness.save,
        ):
            try:
                action()
            except Exception as exc:
                self.notice(f"WARNING: backend cleanup: {exc}")


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description="DeckThere root keyboard backend")
    parser.add_argument("--installed", action="store_true")
    parser.add_argument(
        "--no-keyboard", action="store_true", help="start dashboard without a USB keyboard"
    )
    parser.add_argument("--socket", type=Path, default=Path("/run/deckthere/gui.sock"))
    parser.add_argument("--owner", help="user allowed to connect (development only)")
    parser.add_argument("--group", help="group to own the socket (default: user's primary group)")
    parser.add_argument("--layout", default="us", choices=deckthere_ipc.LAYOUTS)
    parser.add_argument(
        "--no-volume-keys", action="store_true", help="do not take over the Deck volume buttons"
    )
    parser.add_argument(
        "--allow-unverified-gadget",
        action="store_true",
        help="skip the root/hardware safety checks (test environments only)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the ready notice")
    options = parser.parse_args(argv)
    if os.geteuid() != 0 and not options.allow_unverified_gadget:
        parser.error("must run as root")
    import pwd

    if options.installed:
        if os.geteuid() != 0:
            parser.error("installed backend requires root")
        options.socket = Path("/run/deckthere/gui.sock")
        options.layout_store = Path("/home/.deckthere/data/keyboard-layout")
        uid = int(Path("/home/.deckthere/bin/owner-uid").read_text())
        record = pwd.getpwuid(uid)
        options.owner = record.pw_name
    if not options.owner:
        parser.error("--owner is required for development mode")
    try:
        record = pwd.getpwnam(options.owner)
    except KeyError:
        parser.error(f"no such user: {options.owner}")
    options.owner_uid = record.pw_uid
    options.owner_gid = record.pw_gid
    if options.group:
        try:
            options.owner_gid = grp.getgrnam(options.group).gr_gid
        except KeyError:
            parser.error(f"no such group: {options.group}")
    return options


def main(argv=None):
    options = parse_arguments(sys.argv[1:] if argv is None else argv)
    # Constructors clean up after themselves if any hardware step fails.
    backend = Backend(options)

    def stop(signum, frame):
        del signum, frame
        backend.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    backend.run()  # run() releases keys, brightness, volume grab, and the gadget.
    return 0


if __name__ == "__main__":
    sys.exit(main())
