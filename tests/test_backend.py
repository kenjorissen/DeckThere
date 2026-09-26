import json
import os
import select
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # Discover imports tests/ as top-level; reach source modules.

import vhp_backend  # noqa: E402
import vhp_ipc  # noqa: E402


class FakeGadget:
    def __init__(self, shared=True):
        self.read_fd, self.fd = os.pipe()
        self.is_shared = shared
        self.closed = False

    def shared(self):
        return self.is_shared

    def reports(self, count, timeout=1.0):
        wanted = count * 8
        data = b""
        end = time.monotonic() + timeout
        while len(data) < wanted and time.monotonic() < end:
            ready, _, _ = select.select([self.read_fd], [], [], 0.05)
            if ready:
                data += os.read(self.read_fd, wanted - len(data))
        return [data[index : index + 8] for index in range(0, len(data), 8)]

    def close(self):
        if not self.closed:
            for descriptor in (self.read_fd, self.fd):
                os.close(descriptor)
            self.fd = None
            self.closed = True


class OptionalKeyboardTests(unittest.TestCase):
    def test_gui_only_hardware_never_constructs_gadget_but_keeps_volume(self):
        options = SimpleNamespace(no_keyboard=True, no_volume_keys=False)
        with (
            patch.object(vhp_backend, "Gadget") as gadget,
            patch.object(vhp_backend, "Brightness") as brightness,
            patch.object(vhp_backend, "VolumeBridge") as volume,
        ):
            hardware = vhp_backend.default_hardware(options)
            self.assertIsInstance(hardware.gadget, vhp_backend.DisabledKeyboard)
            self.assertIsNone(hardware.gadget.fd)
            self.assertFalse(hardware.gadget.shared())
            hardware.gadget.close()
            gadget.assert_not_called()
            volume.assert_called_once_with(brightness.return_value)

    def test_enable_is_explicit_and_idempotent_on_authenticated_socket(self):
        with (
            patch.object(vhp_backend, "Gadget", side_effect=FakeGadget) as factory,
            Harness(keyboard=False) as harness,
        ):
            client = harness.connect()
            self.assertFalse(client.wait_for("status")["keyboard"])
            client.send({"op": "key", "code": 4, "down": True})
            client.send({"op": "clear"})
            client.send({"op": "status"})
            self.assertEqual(client.wait_for("status")["keys"], 0)
            factory.assert_not_called()
            for _ in range(2):
                client.send({"op": "keyboard_start"})
                self.assertTrue(client.wait_for("status")["keyboard"])
            factory.assert_called_once_with()
            client.send({"op": "key", "code": 4, "down": True})
            self.assertEqual(harness.backend.gadget.reports(1), [bytes([0, 0, 4, 0, 0, 0, 0, 0])])
            client.close()

    def test_stop_clears_reports_and_allows_clean_restart_without_stopping_backend(self):
        with (
            Harness() as harness,
            patch.object(vhp_backend, "Gadget", side_effect=FakeGadget) as factory,
        ):
            client = harness.connect()
            self.assertTrue(client.wait_for("status")["keyboard"])
            # A single batch queues a held key, stops, then tries typing while off.
            client.raw(
                b"".join(
                    vhp_ipc.encode(message)
                    for message in (
                        {"op": "key", "code": 225, "down": True},
                        {"op": "keyboard_stop"},
                        {"op": "key", "code": 4, "down": True},
                        {"op": "status"},
                    )
                )
            )
            self.assertFalse(client.wait_for("status")["keyboard"])
            state = client.wait_for("status")
            self.assertFalse(state["shared"])
            self.assertEqual(state["keys"], 0)
            self.assertFalse(harness.backend.reports)
            self.assertTrue(harness.gadget.closed)
            self.assertFalse(harness.backend.stopping)
            self.assertIs(harness.backend.brightness, harness.brightness)
            self.assertEqual(harness.brightness.saves, 0)
            client.send({"op": "keyboard_stop"})  # Idempotent, no gadget reconstruction.
            self.assertFalse(client.wait_for("status")["keyboard"])
            factory.assert_not_called()
            client.send({"op": "keyboard_start"})
            self.assertTrue(client.wait_for("status")["keyboard"])
            client.send({"op": "key", "code": 5, "down": True})
            self.assertEqual(harness.backend.gadget.reports(1), [bytes([0, 0, 5, 0, 0, 0, 0, 0])])
            factory.assert_called_once_with()
            client.send({"op": "ping"})
            self.assertEqual(client.wait_for("pong"), {"op": "pong"})
            client.close()

    def test_stop_failure_is_reported_and_cleanup_can_be_retried(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "key", "code": 4, "down": True})
            self.assertEqual(harness.gadget.reports(1), [bytes([0, 0, 4, 0, 0, 0, 0, 0])])
            with patch.object(harness.gadget, "close", side_effect=OSError("mock failure")):
                client.send({"op": "keyboard_stop"})
                self.assertEqual(client.wait_for("keyboard_error"), {"op": "keyboard_error"})
                self.assertIs(harness.backend.gadget, harness.gadget)
                self.assertFalse(harness.backend.stopping)
                self.assertEqual(harness.gadget.reports(1), [bytes(8)])
            client.send({"op": "keyboard_stop"})
            self.assertFalse(client.wait_for("status")["keyboard"])
            self.assertTrue(harness.gadget.closed)
            client.close()

    def test_failed_enable_leaves_dashboard_alive_without_a_keyboard(self):
        with (
            patch.object(vhp_backend, "Gadget", side_effect=RuntimeError("unavailable")),
            Harness(keyboard=False) as harness,
        ):
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "keyboard_start"})
            self.assertEqual(client.wait_for("keyboard_error"), {"op": "keyboard_error"})
            client.send({"op": "status"})
            self.assertFalse(client.wait_for("status")["keyboard"])
            self.assertFalse(harness.backend.stopping)
            client.close()


class FakeBrightness:
    def __init__(self, percent=1):
        self.percent = percent
        self.changes = []
        self.saves = 0
        self.checks = 0

    def maintain(self):
        self.checks += 1
        return None

    def change(self, delta):
        self.changes.append(delta)
        self.percent = min(100, max(0, self.percent + delta))

    def save(self):
        self.saves += 1


class Client:
    def __init__(self, socket_object):
        self.socket = socket_object
        self.buffer = b""

    def send(self, message):
        self.socket.sendall(vhp_ipc.encode(message))

    def raw(self, data):
        self.socket.sendall(data)

    def message(self, timeout=3.0):
        end = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise AssertionError("no message from backend")
            self.socket.settimeout(remaining)
            try:
                data = self.socket.recv(4096)
            except TimeoutError:
                continue
            if not data:
                raise AssertionError("backend closed the connection")
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def wait_for(self, op, timeout=3.0):
        """Read until the wanted operation arrives; periodic status may interleave."""
        end = time.monotonic() + timeout
        while True:
            message = self.message(max(0.05, end - time.monotonic()))
            if message["op"] == op:
                return message

    def is_closed(self, timeout=2.0):
        self.socket.settimeout(timeout)
        try:
            return self.socket.recv(1) == b""
        except (TimeoutError, ConnectionResetError):
            return False

    def close(self):
        self.socket.close()


class Harness:
    """Runs a real backend in-process against fake hardware."""

    def __init__(self, shared=True, owner_uid=None, keyboard=True, **overrides):
        self.temporary = tempfile.TemporaryDirectory()
        self.gadget = FakeGadget(shared) if keyboard else vhp_backend.DisabledKeyboard()
        self.brightness = FakeBrightness()
        self.options = SimpleNamespace(
            socket=Path(self.temporary.name) / "gui.sock",
            owner_uid=os.getuid() if owner_uid is None else owner_uid,
            owner_gid=os.getgid(),
            layout="us",
            no_volume_keys=True,
            quiet=True,
            **overrides,
        )
        self.backend = vhp_backend.Backend(
            self.options, vhp_backend.Hardware(self.gadget, self.brightness, None)
        )
        self.error = None
        self.thread = None

    def __enter__(self):
        def run():
            try:
                self.backend.run()
            except BaseException as exc:  # surfaced below; never swallowed silently
                self.error = exc

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        # Wait for the finished socket, not merely for the file to appear: bind()
        # creates it before chown/chmod, and clients cannot connect until listen(),
        # which happens after both.
        end = time.monotonic() + 3
        while True:
            try:
                info = self.options.socket.lstat()
                ready = (info.st_mode & 0o777) == 0o600 and info.st_uid == self.options.owner_uid
            except FileNotFoundError:
                ready = False
            if ready:
                break
            if time.monotonic() > end:
                self.__exit__()
                raise AssertionError(f"backend did not start: {self.error!r}")
            time.sleep(0.01)
        return self

    def __exit__(self, *exc_info):
        if self.thread is not None:
            self.backend.stopping = True
            self.thread.join(timeout=3)
        self.temporary.cleanup()
        return False

    def connect(self):
        # The socket file appears at bind() but only accepts after listen(), so a
        # fast client can arrive first. Retry briefly instead of racing.
        end = time.monotonic() + 3
        while True:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(3)
            try:
                client.connect(str(self.options.socket))
            except (ConnectionRefusedError, FileNotFoundError):
                client.close()
                if time.monotonic() > end:
                    raise
                time.sleep(0.01)
                continue
            return Client(client)


class SocketTests(unittest.TestCase):
    def test_socket_is_private_and_owned_by_the_configured_user(self):
        with Harness() as harness:
            info = harness.options.socket.lstat()
            self.assertTrue(stat.S_ISSOCK(info.st_mode))
            self.assertEqual(info.st_mode & 0o777, 0o600)
            self.assertEqual(
                (info.st_uid, info.st_gid), (harness.options.owner_uid, harness.options.owner_gid)
            )

    def test_a_peer_that_is_not_the_configured_user_is_rejected_silently(self):
        # Exercise the SO_PEERCRED check directly: binding a socket owned by a
        # foreign uid needs root, but the credential decision does not.
        with Harness() as harness:
            harness.backend.options.owner_uid = os.getuid() + 1
            ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(theirs.close)
            self.addCleanup(ours.close)

            class OneShotServer:
                def accept(self):
                    return ours, None

            self.assertIsNone(harness.backend.accept(OneShotServer()))
            self.assertEqual(theirs.recv(1), b"")

    def test_a_failed_bind_leaves_no_socket_behind(self):
        # A leftover socket would be trusted by the next run.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gui.sock"
            options = SimpleNamespace(
                socket=path,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
                layout="us",
                no_volume_keys=True,
            )
            backend = vhp_backend.Backend(
                options, vhp_backend.Hardware(FakeGadget(), FakeBrightness(), None)
            )
            with patch.object(vhp_backend.os, "chown", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    backend.run()
            self.assertFalse(path.exists())
            self.assertTrue(backend.gadget.closed)

    def test_non_socket_at_the_path_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gui.sock"
            path.write_text("not a socket")
            options = SimpleNamespace(
                socket=path,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
                layout="us",
                no_volume_keys=True,
            )
            backend = vhp_backend.Backend(
                options, vhp_backend.Hardware(FakeGadget(), FakeBrightness(), None)
            )
            with self.assertRaises(SystemExit):
                backend.run()
            self.assertEqual(path.read_text(), "not a socket")

    def test_stop_removes_the_socket_and_exits_cleanly(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "stop"})
            harness.thread.join(timeout=3)
            self.assertFalse(harness.thread.is_alive())
            self.assertIsNone(harness.error)
            self.assertFalse(harness.options.socket.exists())
            self.assertTrue(harness.gadget.closed)
            self.assertGreaterEqual(harness.brightness.saves, 1)
            client.close()

    def test_only_one_client_is_served_at_a_time(self):
        with Harness() as harness:
            first = harness.connect()
            first.wait_for("status")
            second = harness.connect()
            self.assertTrue(second.is_closed())
            second.close()
            first.send({"op": "ping"})
            first.wait_for("pong")
            first.close()


class ProtocolTests(unittest.TestCase):
    def test_status_is_sent_on_connect(self):
        with Harness() as harness:
            client = harness.connect()
            status = client.wait_for("status")
            self.assertTrue(status["shared"])
            self.assertEqual(status["percent"], 1)
            self.assertEqual(status["layout"], "us")
            self.assertEqual(status["keys"], 0)
            self.assertIn("stopping", status)
            client.close()

    def test_ping_and_layout_round_trip(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "ping"})
            client.wait_for("pong")
            client.send({"op": "layout", "layout": "de"})
            while True:
                if client.message()["layout"] == "de":
                    break
            client.close()

    def test_presses_produce_standard_hid_reports(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "key", "code": 4, "down": True})
            self.assertEqual(harness.gadget.reports(1), [bytes([0, 0, 4, 0, 0, 0, 0, 0])])
            client.send({"op": "key", "code": 4, "down": False})
            self.assertEqual(harness.gadget.reports(1), [bytes(8)])
            client.close()

    def test_modifier_plus_letter_is_a_single_report(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "key", "code": 225, "down": True})
            client.send({"op": "key", "code": 4, "down": True})
            reports = harness.gadget.reports(2)
            self.assertEqual(reports[1], bytes([0b00000010, 0, 4, 0, 0, 0, 0, 0]))
            client.close()

    def test_keystrokes_are_withheld_until_the_pc_has_bound_the_keyboard(self):
        with Harness(shared=False) as harness:
            client = harness.connect()
            self.assertFalse(client.wait_for("status")["shared"])
            client.send({"op": "key", "code": 4, "down": True})
            client.send({"op": "ping"})
            client.wait_for("pong")
            self.assertFalse(select.select([harness.gadget.read_fd], [], [], 0.1)[0])
            client.close()

    def test_clear_releases_every_held_key(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "key", "code": 225, "down": True})
            client.send({"op": "key", "code": 42, "down": True})
            harness.gadget.reports(2)
            client.send({"op": "clear"})
            self.assertEqual(harness.gadget.reports(1), [bytes(8)])
            client.close()

    def test_disconnect_releases_held_keys_on_the_wire(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            client.send({"op": "key", "code": 4, "down": True})
            harness.gadget.reports(1)
            client.close()
            self.assertEqual(harness.gadget.reports(1), [bytes(8)])

    def test_rollover_beyond_six_keys_is_dropped_without_disconnecting(self):
        with Harness() as harness:
            client = harness.connect()
            client.wait_for("status")
            for code in range(4, 11):  # seven non-modifier keys
                client.send({"op": "key", "code": code, "down": True})
            client.send({"op": "ping"})
            client.wait_for("pong")
            # The seventh press is refused, so exactly six reports reach the PC.
            reports = harness.gadget.reports(6)
            self.assertEqual(len(reports), 6)
            self.assertEqual(sum(byte != 0 for byte in reports[-1][2:]), 6)
            client.close()

    def test_malformed_oversized_and_unknown_messages_drop_the_client(self):
        for payload in (
            b"not json\n",
            b"{}\n",
            b'{"op":"exec"}\n',
            b'{"op":"key","code":4,"down":1}\n',
            b"x" * 5000,
            b'{"op":"status"} extra\n',
            b'{"op":"status","path":"/etc/shadow"}\n',
        ):
            with self.subTest(payload=payload[:24]):
                with Harness() as harness:
                    client = harness.connect()
                    try:
                        client.wait_for("status")
                        client.raw(payload)
                        self.assertTrue(client.is_closed())
                    finally:
                        client.close()


class ArgumentTests(unittest.TestCase):
    def test_layout_and_owner_are_validated(self):
        options = vhp_backend.parse_arguments(
            [
                "--owner",
                os.environ.get("USER", "root"),
                "--layout",
                "us",
                "--allow-unverified-gadget",
            ]
        )
        self.assertEqual(options.layout, "us")
        self.assertGreater(options.owner_uid, -1)
        for bad in (
            ["--owner", "vhp-no-such-user"],
            ["--owner", "root", "--layout", "dvorak"],
            ["--owner", "root", "--group", "vhp-no-such-group"],
        ):
            with self.subTest(arguments=bad):
                with self.assertRaises(SystemExit):
                    vhp_backend.parse_arguments(bad + ["--allow-unverified-gadget"])

    def test_root_is_required_without_the_test_escape_hatch(self):
        if os.geteuid() == 0:
            self.skipTest("already root")
        with self.assertRaises(SystemExit):
            vhp_backend.parse_arguments(["--owner", os.environ.get("USER", "root")])

    def test_socket_path_and_volume_opt_out_are_configurable(self):
        options = vhp_backend.parse_arguments(
            [
                "--owner",
                os.environ.get("USER", "root"),
                "--socket",
                "/tmp/vhp-test.sock",
                "--allow-unverified-gadget",
                "--no-volume-keys",
            ]
        )
        self.assertEqual(str(options.socket), "/tmp/vhp-test.sock")
        self.assertTrue(options.no_volume_keys)


if __name__ == "__main__":
    unittest.main()
