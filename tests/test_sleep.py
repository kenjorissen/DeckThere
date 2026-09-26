"""Deterministic idle policy tests; no real service, display, USB or suspend."""

import ctypes
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import deckthere_activity as hardware
import deckthere_sleep as policy


class Reports(unittest.TestCase):
    def frame(self, length=64):
        data = bytearray(64)
        data[:4] = bytes((1, 0, 9, length))
        return data

    def test_frame_counter_and_gyro_do_not_count(self):
        for length in (56, 64):
            data = self.frame(length)
            struct.pack_into("<I", data, 4, 123456)
            struct.pack_into("<3h", data, 30, 32000, -32000, 9000)
            self.assertFalse(hardware.report_active(data))

    def test_buttons_holds_and_touch(self):
        for offset, bit in ((8, 7), (9, 0), (10, 3), (10, 4), (13, 1), (13, 6), (14, 2)):
            data = self.frame()
            data[offset] = 1 << bit
            for _ in range(3):
                self.assertTrue(hardware.report_active(data))

    def test_both_analog_triggers_and_all_stick_axes_include_steady_holds(self):
        for offset in (44, 46):
            data = self.frame()
            struct.pack_into("<H", data, offset, hardware.TRIGGER_DEADZONE + 1)
            self.assertTrue(hardware.report_active(data))
            self.assertTrue(hardware.report_active(data))
        for offset in (48, 50, 52, 54):
            for direction in (-1, 1):
                data = self.frame()
                struct.pack_into("<h", data, offset, direction * (hardware.STICK_DEADZONE + 1))
                self.assertTrue(hardware.report_active(data))

    def test_noise_is_not_activity_and_unknown_report_is_not_idle(self):
        data = self.frame()
        struct.pack_into("<4h", data, 48, 1921, -1668, 200, 0)
        self.assertFalse(hardware.report_active(data))
        for corrupt in (data[:63], bytearray(64), self.frame(60)):
            with self.assertRaises(ValueError):
                hardware.report_active(corrupt)


class ObservationTests(unittest.TestCase):
    def observer(self):
        observer = hardware.Observer.__new__(hardware.Observer)
        observer.fd = 123
        observer.identity = ("3-3", 3, 2)
        observer.activity = 1
        observer.last_report = 0
        observer.was_active = False
        observer.next_identity = 999
        observer.local = Mock()
        observer.local.poll.return_value = False
        observer.header = ctypes.create_string_buffer(64)
        observer.payload = ctypes.create_string_buffer(64)
        observer.args = b""
        return observer

    def poll(self, observer, frames=(), dropped=0, now=10):
        frames = iter(frames)

        def ioctl(fd, operation, buffer, *args):
            if operation == hardware.GETX:
                try:
                    device, payload, status = next(frames)
                except StopIteration:
                    raise BlockingIOError from None
                header = bytearray(64)
                header[8:12] = bytes((ord("C"), 1, 0x83, device))
                struct.pack_into("=H", header, 12, 3)
                struct.pack_into("=iII", header, 28, status, 64, 64)
                ctypes.memmove(observer.header, bytes(header), 64)
                ctypes.memmove(observer.payload, bytes(payload), 64)
            elif operation == hardware.STATS:
                buffer[:] = struct.pack("=II", 0, dropped)

        with (
            patch.object(hardware.fcntl, "ioctl", side_effect=ioctl),
            patch.object(hardware, "volume_activity", return_value=0),
        ):
            return observer.poll(now)

    def test_only_controller_payload_is_decoded_and_stream_must_be_fresh(self):
        observer = self.observer()
        frame = Reports().frame()
        self.assertTrue(self.poll(observer, [(3, bytes(64), 0), (2, frame, 0)])["healthy"])
        self.assertFalse(self.poll(observer, now=12)["healthy"])
        self.assertEqual(self.poll(observer, [(2, frame, 0)], now=12.2)["activity"], 12.2)

    def test_loss_unknown_format_errors_and_identity_changes_are_not_idle(self):
        for frames, dropped in (
            ([(2, bytes(64), 0)], 0),
            ([(2, Reports().frame(), -1)], 0),
            ([], 1),
        ):
            with self.assertRaises((OSError, ValueError)):
                self.poll(self.observer(), frames, dropped)
        observer = self.observer()
        observer.next_identity = 0
        with patch.object(hardware, "controller", return_value=("3-3", 3, 4)):
            with self.assertRaises(OSError):
                observer.poll(10)

    def test_held_trigger_and_release_refresh_activity(self):
        observer = self.observer()
        frame = Reports().frame()
        struct.pack_into("<H", frame, 46, 20000)
        for now in (10, 11, 12):
            self.assertEqual(self.poll(observer, [(2, frame, 0)], now=now)["activity"], now)
        self.assertEqual(self.poll(observer, [(2, Reports().frame(), 0)], now=13)["activity"], 13)
        self.assertEqual(self.poll(observer, [(2, Reports().frame(), 0)], now=13.5)["activity"], 13)

    def test_local_held_state_is_queried_without_grabbing_and_loss_is_not_idle(self):
        local = hardware.LocalInputs.__new__(hardware.LocalInputs)
        local.devices = [(123, 2), (124, 0)]

        def slots(fd, request, data, *args):
            data[:] = struct.pack("=3i", 57, -1, 99)

        with (
            patch.object(hardware.os, "read", side_effect=BlockingIOError),
            patch.object(hardware.fcntl, "ioctl", side_effect=slots),
            patch.object(hardware, "ioctl_bytes", return_value=bytes(96)),
        ):
            self.assertTrue(local.poll())
        local.devices = [(124, 0)]
        with (
            patch.object(hardware.os, "read", side_effect=BlockingIOError),
            patch.object(hardware, "ioctl_bytes", return_value=b"\\x01" + bytes(95)),
        ):
            self.assertTrue(local.poll())
        for data in (b"", b"short", hardware.EVENT.pack(0, 0, 0, 3, 0)):
            with patch.object(hardware.os, "read", return_value=data):
                with self.assertRaises(OSError):
                    local.poll()

    def test_server_dormant_without_authorized_client_and_expires_lease(self):
        for peer in (None, 1001, 1000):
            with tempfile.TemporaryDirectory() as directory:
                handlers = {}
                clock = [0]
                server = MagicMock()
                connection = MagicMock()
                connection.getsockopt.return_value = struct.pack("3i", 3, peer or 1000, 1000)
                server.accept.return_value = (connection, None)
                observer = Mock()
                observer.poll.return_value = {"healthy": True, "activity": 0}
                factory = Mock(return_value=observer)
                calls = [0]

                def select_ready(*args):
                    calls[0] += 1
                    if calls[0] == 1 and peer is not None:
                        return ([server], [], [])
                    if calls[0] in (2, 3) and peer == 1000:
                        clock[0] = 1 if calls[0] == 2 else 5
                    else:
                        handlers[hardware.signal.SIGTERM]()
                    return ([], [], [])

                with (
                    patch.object(hardware.socket, "socket", return_value=server),
                    patch.object(hardware.os, "chown"),
                    patch.object(
                        hardware.signal,
                        "signal",
                        side_effect=lambda sig, handler: handlers.update({sig: handler}),
                    ),
                    patch.object(hardware.time, "monotonic", side_effect=lambda: clock[0]),
                    patch.object(hardware.select, "select", side_effect=select_ready),
                    patch.object(hardware, "Observer", factory),
                ):
                    hardware.serve(1000, Path(directory) / "activity.sock")
                self.assertEqual(factory.call_count, int(peer == 1000))
                if peer == 1000:
                    observer.close.assert_called_once()
                connection.recv.assert_not_called()  # No privileged command protocol.


class SleepPolicy(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = policy.initialize(self.base, 100)
        self.observe = Mock(return_value={"healthy": True, "activity": 100})

    def enable(self):
        policy.save_data(self.base / "sleep-minutes", 5)
        policy.tick(self.base, 100, self.observe)

    def armed(self):
        self.enable()
        state = policy.read_data(self.base / policy.STATE)
        state["tick"] = 399
        policy.save_data(self.base / policy.STATE, state)

    def step(self, now, acknowledge=True):
        value = policy.tick(self.base, now, self.observe)
        if acknowledge and value["status"] == "warning":
            policy.save_data(
                self.base / "sleep-warning-seen", {"warning": value["warning"], "at": now}
            )
        return value

    def due(self):
        self.armed()
        for now in range(400, 431):
            value = self.step(now)
        self.assertEqual(value["status"], "due")
        return value

    def test_never_default_does_not_contact_observer(self):
        self.assertEqual(self.step(101)["status"], "off")
        self.observe.assert_not_called()

    def test_one_minute_option_retains_full_cancellation_warning(self):
        policy.save_data(self.base / "sleep-minutes", 1)
        self.step(100)
        for now in range(101, 160):
            self.assertEqual(self.step(now)["status"], "armed")
        self.assertEqual(self.step(160)["remaining"], 30)
        for now in range(161, 190):
            self.assertEqual(self.step(now)["status"], "warning")
        self.assertEqual(self.step(190)["status"], "due")

    def test_thirty_seconds_of_visible_warning_required(self):
        self.armed()
        self.assertEqual(self.step(400)["remaining"], 30)
        for now in range(401, 430):
            self.assertEqual(self.step(now)["status"], "warning")
        self.assertEqual(self.step(430)["status"], "due")

    def test_missing_warning_renderer_or_stale_ack_prevents_sleep(self):
        self.armed()
        for now in range(400, 435):
            self.assertNotEqual(self.step(now, acknowledge=False)["status"], "due")
        self.armed()
        self.step(400)
        for now in range(401, 435):
            self.assertNotEqual(self.step(now, acknowledge=False)["status"], "due")

    def test_hardware_or_local_activity_cancels_warning(self):
        for local in (False, True):
            self.armed()
            self.step(400)
            if local:
                policy.save_data(self.base / "sleep-activity", 401)
            else:
                self.observe.return_value["activity"] = 401
            result = self.step(401)
            self.assertIsNone(result["warning"])
            self.assertEqual(result["last"], 401)
            self.assertEqual(result["status"], "armed")

    def test_input_arriving_during_snapshot_is_activity_not_monitor_failure(self):
        self.enable()
        self.observe.return_value["activity"] = 101.05
        with patch.object(policy.time, "monotonic", side_effect=[101, 101.1]):
            value = policy.tick(self.base, observe=self.observe)
        self.assertEqual(value["status"], "armed")
        self.assertEqual(value["last"], 101)

    def test_hold_continuously_resets_idle_clock(self):
        self.enable()
        for now in range(101, 450):
            self.observe.return_value["activity"] = now
            self.assertEqual(self.step(now)["status"], "armed")

    def test_lost_telemetry_unknown_data_and_late_supervisor_fail_awake(self):
        self.armed()
        self.step(400)
        self.observe.return_value = {"healthy": False, "activity": 100}
        value = self.step(401)
        self.assertIsNone(value["warning"])
        self.assertEqual(value["last"], 401)
        self.observe.side_effect = OSError("missing socket")
        self.assertEqual(self.step(402)["status"], "waiting")
        self.observe.side_effect = None
        self.observe.return_value = {"healthy": True, "activity": 100}
        self.assertEqual(self.step(440)["last"], 440)

    def test_live_interval_changes_and_restart_reset_warning(self):
        self.armed()
        self.step(400)
        policy.save_data(self.base / "sleep-minutes", 15)
        self.assertIsNone(self.step(401)["warning"])
        policy.initialize(self.base, 402)
        self.assertNotEqual(self.step(403)["status"], "due")

    def test_bounded_nofollow_nonblocking_preferences_and_exact_values(self):
        path = self.base / "sleep-minutes"
        for raw in ("true", "-1", "5.0", "120", "NaN", "[" * 2000, "x" * 4097):
            path.write_text(raw)
            self.assertEqual(policy.read_minutes(self.base), 0)
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(policy.read_minutes(self.base), 0)
        path.unlink()
        target = self.base / "target"
        target.write_text("60")
        path.symlink_to(target)
        self.assertEqual(policy.read_minutes(self.base), 0)
        with patch.object(policy.time, "monotonic", return_value=101):
            policy.save_minutes(15, self.base)
        self.assertEqual(target.read_text(), "60")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(policy.read_minutes(self.base), 15)
        for bad in (True, 5.0, -1, 100):
            with self.assertRaises(ValueError):
                policy.save_minutes(bad, self.base)

    def test_invalid_or_future_cancellation_and_state_fail_awake(self):
        self.armed()
        policy.save_data(self.base / "sleep-activity", 999999)
        self.assertEqual(self.step(400)["last"], 400)
        (self.base / policy.STATE).write_text('{"last":NaN,"tick":0}')
        self.assertNotEqual(self.step(401)["status"], "due")

    def test_suspend_only_after_clean_stop_and_once(self):
        self.due()
        run = Mock(
            side_effect=[
                SimpleNamespace(returncode=0, stdout="ActiveState=inactive\nResult=success\n"),
                SimpleNamespace(returncode=0),
            ]
        )
        with patch.object(policy.time, "monotonic", return_value=435):
            self.assertTrue(policy.suspend_after_cleanup(self.base, run))
            self.assertFalse(policy.suspend_after_cleanup(self.base, run))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1].args[0], ["/usr/bin/systemctl", "--no-ask-password", "suspend"]
        )

    def test_active_failed_cleanup_cancelled_or_disabled_never_suspend(self):
        for properties in (
            "ActiveState=active\nResult=success",
            "ActiveState=inactive\nResult=timeout",
            "",
        ):
            self.due()
            run = Mock(return_value=SimpleNamespace(returncode=0, stdout=properties))
            with patch.object(policy.time, "monotonic", return_value=435):
                self.assertFalse(policy.suspend_after_cleanup(self.base, run))
            self.assertEqual(run.call_count, 1)
        for name, value in (("sleep-activity", 432), ("sleep-minutes", 0)):
            self.due()
            policy.save_data(self.base / name, value)
            with patch.object(policy.time, "monotonic", return_value=435):
                run = Mock()
                self.assertFalse(policy.suspend_after_cleanup(self.base, run))
                run.assert_not_called()
            (self.base / name).unlink()

    def test_cancellation_during_service_query_wins(self):
        for signal_only in (False, True):
            (self.base / "sleep-activity").unlink(missing_ok=True)
            self.due()
            stopped = [False]

            def run(*args, **kwargs):
                if signal_only:
                    stopped[0] = True
                else:
                    policy.save_data(self.base / "sleep-activity", 434)
                return SimpleNamespace(
                    returncode=0, stdout="ActiveState=inactive\nResult=success\n"
                )

            runner = Mock(side_effect=run)
            with patch.object(policy.time, "monotonic", return_value=435):
                self.assertFalse(
                    policy.suspend_after_cleanup(self.base, runner, cancelled=lambda: stopped[0])
                )
            self.assertEqual(runner.call_count, 1)

    def test_snapshot_requires_root_peer_and_bounded_valid_reply(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        with patch.object(policy.socket, "socket", return_value=connection):
            connection.getsockopt.return_value = struct.pack("3i", 1, 1000, 1000)
            with self.assertRaises(OSError):
                policy.snapshot(100)
            connection.getsockopt.return_value = struct.pack("3i", 1, 0, 0)
            for raw in (b"x" * 257, b"{}\n", b'{"healthy":true,"activity":NaN}\n'):
                connection.recv.return_value = raw
                with self.assertRaises(ValueError):
                    policy.snapshot(100)
            connection.recv.return_value = (
                json.dumps({"healthy": True, "activity": 1}).encode() + b"\n"
            )
            self.assertTrue(policy.snapshot(100)["healthy"])
