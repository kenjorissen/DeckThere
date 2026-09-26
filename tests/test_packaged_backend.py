"""Safety regressions for installed backend; no hardware/root access."""

import os
import select
import time
import unittest
from unittest.mock import patch

from test_backend import Harness

import deckthere_backend
import deckthere_hardware


class FakeVolume:
    def __init__(self, brightness):
        self.source, self.writer = os.pipe()
        self.brightness = brightness
        self.calls = 0
        self.closed = False

    def process(self):
        for event in os.read(self.source, 4096):
            if event in (ord("+"), ord("-")):
                self.brightness.change(1 if event == ord("+") else -1)
        self.calls += 1

    def close(self):
        self.closed = True
        os.close(self.source)
        os.close(self.writer)


class InstalledBackendTests(unittest.TestCase):
    def test_existing_loop_services_brightness_watch_even_when_idle(self):
        with Harness() as harness:
            deadline = time.monotonic() + 2
            while not harness.brightness.checks and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreater(harness.brightness.checks, 0)
            self.assertIsNone(harness.error)

    def test_volume_fd_is_serviced_and_released(self):
        harness = Harness()
        volume = FakeVolume(harness.brightness)
        harness.backend.volume = volume
        with harness:
            os.write(volume.writer, b"+")
            end = time.monotonic() + 2
            while not volume.calls and time.monotonic() < end:
                time.sleep(0.01)
            self.assertEqual(volume.calls, 1)
            self.assertEqual(harness.brightness.changes, [1])
        self.assertTrue(volume.closed)
        self.assertIsNone(harness.error)

    def test_volume_changes_publish_status_without_waiting_for_periodic_tick(self):
        harness = Harness()
        volume = FakeVolume(harness.brightness)
        harness.backend.volume = volume
        with patch.object(deckthere_backend, "STATUS_INTERVAL", 60), harness:
            client = harness.connect()
            self.addCleanup(client.close)
            self.assertEqual(client.wait_for("status")["percent"], 1)
            # A single read batch produces one update with its final percentage.
            os.write(volume.writer, b"+++")
            self.assertEqual(client.wait_for("status", timeout=1)["percent"], 4)
            self.assertFalse(client.buffer)
            self.assertFalse(select.select([client.socket], [], [], 0.1)[0])
            os.write(volume.writer, b"-")
            self.assertEqual(client.wait_for("status", timeout=1)["percent"], 3)
            self.assertIsNone(harness.error)

    def test_unchanged_volume_events_do_not_send_extra_status(self):
        for percent, event in ((1, b"."), (100, b"+"), (0, b"-")):
            with self.subTest(percent=percent, event=event):
                harness = Harness()
                harness.brightness.percent = percent
                volume = FakeVolume(harness.brightness)
                harness.backend.volume = volume
                with patch.object(deckthere_backend, "STATUS_INTERVAL", 60), harness:
                    client = harness.connect()
                    self.addCleanup(client.close)
                    client.wait_for("status")
                    os.write(volume.writer, event)
                    deadline = time.monotonic() + 2
                    while not volume.calls and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(volume.calls, 1)
                    client.send({"op": "ping"})
                    self.assertEqual(client.message()["op"], "pong")
                    self.assertFalse(client.buffer)
                    self.assertFalse(select.select([client.socket], [], [], 0.1)[0])
                    self.assertIsNone(harness.error)

    def test_installed_ui_disconnect_tears_down_hardware(self):
        with Harness(installed=True) as harness:
            client = harness.connect()
            client.wait_for("status")
            client.close()
            harness.thread.join(timeout=2)
            self.assertFalse(harness.thread.is_alive())
            self.assertTrue(harness.gadget.closed)
            self.assertFalse(harness.options.socket.exists())
            self.assertIsNone(harness.error)

    def test_reconnect_discards_partial_previous_frame(self):
        with Harness() as harness:
            first = harness.connect()
            first.wait_for("status")
            first.raw(b'{"op":')
            first.close()
            end = time.monotonic() + 2
            while harness.backend.connection is not None and time.monotonic() < end:
                time.sleep(0.01)
            second = harness.connect()
            self.addCleanup(second.close)
            second.wait_for("status")
            second.send({"op": "ping"})
            second.wait_for("pong")

    def test_ownership_is_checked_at_write_even_before_next_status(self):
        harness = Harness()
        try:
            backend = harness.backend
            backend.shared = True
            backend.press(4, True)
            self.assertEqual(len(backend.reports), 1)
            harness.gadget.is_shared = False
            backend.flush_report()
            self.assertFalse(select.select([harness.gadget.read_fd], [], [], 0)[0])
            self.assertFalse(backend.reports)
            self.assertFalse(backend.keys.keys)
            backend.handle({"op": "clear"})
            backend.disconnect()
            self.assertFalse(select.select([harness.gadget.read_fd], [], [], 0)[0])
        finally:
            harness.backend.close(None)
            harness.temporary.cleanup()

    def test_backpressure_preserves_report_order_and_short_write_fails(self):
        harness = Harness()
        try:
            backend = harness.backend
            backend.press(4, True)
            backend.press(4, False)
            with patch("deckthere_backend.os.write", side_effect=BlockingIOError):
                backend.flush_report()
            self.assertEqual(len(backend.reports), 2)
            backend.flush_report()
            backend.flush_report()
            self.assertEqual(harness.gadget.reports(2), [bytes([0, 0, 4, 0, 0, 0, 0, 0]), bytes(8)])
            backend.press(4, True)
            with patch("deckthere_backend.os.write", return_value=4), self.assertRaises(OSError):
                backend.flush_report()
        finally:
            harness.backend.close(None)
            harness.temporary.cleanup()

    def test_cleanup_failure_still_closes_gadget(self):
        harness = Harness()
        try:
            with patch.object(harness.brightness, "save", side_effect=OSError("disk full")):
                harness.backend.close(None)
            self.assertTrue(harness.gadget.closed)
        finally:
            harness.temporary.cleanup()


class VolumeEventsTests(unittest.TestCase):
    def test_volume_is_consumed_and_other_keys_are_forwarded(self):
        harness = Harness()
        source, writer = os.pipe()
        output, virtual = os.pipe()
        bridge = deckthere_hardware.VolumeBridge.__new__(deckthere_hardware.VolumeBridge)
        bridge.source, bridge.virtual = source, virtual
        bridge.brightness = harness.brightness
        bridge.held = set()
        bridge.dropped = False
        events = [
            (1, 115, 1),
            (1, 115, 2),
            (1, 115, 0),
            (1, 30, 1),
            (0, 0, 0),
            (1, 30, 0),
            (0, 0, 0),
        ]
        try:
            os.write(
                writer, b"".join(deckthere_hardware.EVENT.pack(0, 0, *event) for event in events)
            )
            bridge.process()
            forwarded = list(deckthere_hardware.EVENT.iter_unpack(os.read(output, 4096)))
            self.assertEqual([item[2:] for item in forwarded], events[3:])
            self.assertEqual(harness.brightness.changes, [1, 1])
            self.assertFalse(bridge.held)
            self.assertEqual(harness.brightness.saves, 1)
        finally:
            for fd in (source, writer, output, virtual):
                os.close(fd)
            harness.backend.close(None)
            harness.temporary.cleanup()
