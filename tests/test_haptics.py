"""Connection edges and evdev output, without real hardware or privileges."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import deckthere_haptics as h  # noqa: E402


class HapticsTests(unittest.TestCase):
    def test_tcp_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tcp = root / "tcp"
            header = " sl local_address rem_address st\n"
            tcp.write_text(header + "0: 0100007F:1D97 0200007F:AAAA 01\n")
            self.assertIs(h.virtualhere_clients(root), True)
            tcp.write_text(header + "0: 0100007F:1D97 0200007F:AAAA 06\n")
            self.assertIs(h.virtualhere_clients(root), False)
            (root / "tcp6").write_text(header + "0: 00000000:1D97 00000001:AAAA 01\n")
            self.assertIs(h.virtualhere_clients(root), True)
            (root / "tcp6").write_text("garbage")
            self.assertIsNone(h.virtualhere_clients(root))
            tcp.unlink()
            self.assertIsNone(h.virtualhere_clients(root))

    def test_first_and_last_edges_are_debounced(self):
        state = h.Connections()
        samples = [
            (False, 0),
            (True, 1),
            (True, 2),
            (True, 3),
            (False, 4),
            (True, 4.5),
            (False, 5),
            (False, 6),
            (False, 7),
        ]
        self.assertEqual([state.update(p, t) for p, t in samples], [0, 0, 1, 0, 0, 0, 0, 2, 0])

    def test_unknown_never_causes_disconnect_or_connect(self):
        state = h.Connections()
        self.assertEqual(state.update(None, 0), 0)
        self.assertEqual(state.update(True, 1), 0)
        self.assertEqual(state.update(True, 2), 1)
        self.assertEqual(state.update(False, 3), 0)
        self.assertEqual(state.update(None, 4), 0)
        self.assertEqual(state.update(False, 5), 0)
        self.assertEqual(state.update(False, 6), 2)

    @staticmethod
    def device(fd, number, size):
        if number == 2:
            return struct.pack("=HHHH", 3, 0x28DE, 0x1205, 1)
        if number == 7:
            return b"usb-0000:04:00.3-3/input0\0"
        bit = h.EV_FF if number == 0x20 else h.FF_RUMBLE
        return (1 << bit).to_bytes(size, "little")

    @staticmethod
    def upload(fd, request, effect, mutate):
        assert request == 0x40304580
        assert len(effect) == 48
        assert struct.unpack_from("=Hh", effect) == (h.FF_RUMBLE, -1)
        assert struct.unpack_from("=HH", effect, 10) == (35, 0)
        struct.pack_into("=h", effect, 2, 7)

    def test_one_and_two_short_ticks_and_effect_cleanup(self):
        for count in (1, 2):
            with (
                patch.object(Path, "glob", return_value=[Path("/dev/input/event9")]),
                patch.object(h.os, "open", return_value=99),
                patch.object(h, "ioctl_bytes", side_effect=self.device),
                patch.object(h.fcntl, "ioctl", side_effect=self.upload),
                patch.object(h.os, "write", return_value=h.EVENT.size) as write,
                patch.object(h.os, "close") as close,
            ):
                adapter = h.Haptics()
                adapter.start(count, 0)
                self.assertEqual(write.call_count, 1)
                adapter.advance(0.1)
                adapter.advance(0.15)
                adapter.advance(0.3)
                self.assertEqual(write.call_count, count)
                self.assertEqual(write.call_args.args[1], h.EVENT.pack(0, 0, h.EV_FF, 7, 1))
                close.assert_called_once_with(99)
                self.assertIsNone(adapter.fd)

    def test_other_gamepads_are_never_written(self):
        with (
            patch.object(Path, "glob", return_value=[Path("/dev/input/event9")]),
            patch.object(h.os, "open", return_value=99),
            patch.object(h, "ioctl_bytes", return_value=struct.pack("=HHHH", 3, 0x1234, 1, 1)),
            patch.object(h.fcntl, "ioctl") as upload,
            patch.object(h.os, "write") as write,
            patch.object(h.os, "close") as close,
        ):
            h.Haptics().start(1, 0)
            upload.assert_not_called()
            write.assert_not_called()
            close.assert_called_once_with(99)

    def test_virtual_and_non_rumble_devices_are_skipped(self):
        for rejected_number in (7, 0x20, 0x20 + h.EV_FF):

            def probe(fd, number, size):
                return bytes(size) if number == rejected_number else self.device(fd, number, size)

            with (
                patch.object(Path, "glob", return_value=[Path("/dev/input/event9")]),
                patch.object(h.os, "open", return_value=99),
                patch.object(h, "ioctl_bytes", side_effect=probe),
                patch.object(h.fcntl, "ioctl") as upload,
                patch.object(h.os, "close") as close,
            ):
                adapter = h.Haptics()
                adapter.start(1, 0)
                self.assertIsNone(adapter.fd)
                upload.assert_not_called()
                close.assert_called_once_with(99)

    def test_busy_upload_closes_descriptor_and_can_rediscover(self):
        with (
            patch.object(Path, "glob", return_value=[Path("/dev/input/event9")]),
            patch.object(h.os, "open", return_value=99),
            patch.object(h, "ioctl_bytes", side_effect=self.device),
            patch.object(h.fcntl, "ioctl", side_effect=OSError("busy")),
            patch.object(h.os, "close") as close,
        ):
            adapter = h.Haptics()
            adapter.start(1, 0)
            self.assertIsNone(adapter.fd)
            adapter.start(2, 4)
            self.assertEqual(close.call_count, 2)

    def test_monitor_connect_disconnect_and_shutdown(self):
        handlers = {}
        now = [0.0]

        def sleep(_seconds):
            now[0] += 0.5
            if now[0] >= 3:
                handlers[h.signal.SIGTERM](h.signal.SIGTERM, None)

        with (
            patch.object(h, "Haptics") as adapter,
            patch.object(
                h.signal, "signal", side_effect=lambda sig, fn: handlers.update({sig: fn})
            ),
            patch.object(h.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(h.time, "sleep", side_effect=sleep),
            patch.object(Path, "exists", return_value=False),
            patch.object(h, "virtualhere_clients", side_effect=[True] * 3 + [False] * 3),
        ):
            adapter.return_value.fd = None
            h.monitor()
            self.assertEqual(
                adapter.return_value.start.call_args_list, [call(1, 1.0), call(2, 2.5)]
            )
            adapter.return_value.close.assert_called_once()

    def test_shutdown_during_sample_does_not_emit(self):
        handlers = {}

        def sample():
            handlers[h.signal.SIGTERM](h.signal.SIGTERM, None)
            return True

        with (
            patch.object(h, "Haptics") as adapter,
            patch.object(
                h.signal, "signal", side_effect=lambda sig, fn: handlers.update({sig: fn})
            ),
            patch.object(Path, "exists", return_value=False),
            patch.object(h, "virtualhere_clients", side_effect=sample),
        ):
            h.monitor()
            adapter.return_value.start.assert_not_called()
            adapter.return_value.advance.assert_not_called()
            adapter.return_value.close.assert_called_once()

    def test_stopping_marker_prevents_sampling_and_output(self):
        with (
            patch.object(h, "Haptics") as adapter,
            patch.object(h.signal, "signal"),
            patch.object(Path, "exists", return_value=True),
            patch.object(h, "virtualhere_clients") as sample,
        ):
            h.monitor()
            sample.assert_not_called()
            adapter.return_value.start.assert_not_called()
            adapter.return_value.close.assert_called_once()

    def test_disappearing_device_cancels_second_tick(self):
        adapter = h.Haptics()
        adapter.fd, adapter.effect_id, adapter.remaining = 99, 7, 2
        with (
            patch.object(h.os, "write", side_effect=OSError("gone")) as write,
            patch.object(h.os, "close") as close,
        ):
            adapter.advance(0)
            adapter.advance(1)
            write.assert_called_once()
            close.assert_called_once_with(99)


if __name__ == "__main__":
    unittest.main()
