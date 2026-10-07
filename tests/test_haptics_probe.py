"""Offline checks for the opt-in VirtualHere hook experiment."""

import importlib.util
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "haptics_probe", Path(__file__).resolve().parents[1] / "tools/haptics-hook-probe.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class HookProbeTests(unittest.TestCase):
    def test_install_remove_preserves_unrelated_settings(self):
        data = b"ServerName=DeckThere\r\nLicense=secret\r\nAllowedDevices=28de/1205\r\n"
        installed = probe.configure(data, True)
        self.assertTrue(installed.startswith(data))
        self.assertIn(b"onClientConnect=", installed)
        self.assertIn(b"onClientDisconnect=", installed)
        # Keep changes VirtualHere made during the experiment on removal.
        installed = installed.replace(b"ServerName=DeckThere", b"ServerName=Renamed")
        self.assertEqual(probe.configure(installed, False), data.replace(b"DeckThere", b"Renamed"))

    def test_existing_hooks_are_never_overwritten(self):
        for key in (b"onClientConnect", b"ONCLIENTDISCONNECT"):
            with self.assertRaises(RuntimeError):
                probe.configure(key + b"=custom command\n", True)
            with self.assertRaises(RuntimeError):
                probe.configure(key + b"=custom command\n", False)

    def test_upgrade_moves_connect_to_device_bind_and_preserves_disconnect(self):
        original = b"License=secret\nServerName=DeckThere\n"
        old = probe.configure(original, True)
        upgraded = probe.configure(old, True, bind=True)
        self.assertNotIn(b"onClientConnect=", upgraded)
        self.assertIn(b"onBind.28de.1205=", upgraded)
        self.assertIn(b"--event bind\n", upgraded)
        self.assertIn(probe.hook_lines()[b"onclientdisconnect"], upgraded)
        self.assertEqual(probe.configure(upgraded, False), original)
        self.assertEqual(probe.configure(upgraded, True, bind=True), upgraded)

    def test_bind_upgrade_refuses_custom_or_broader_bind_hooks(self):
        for key in (b"onBind", b"onBind.28de", b"onBind.28de.1205"):
            with self.assertRaises(RuntimeError):
                probe.configure(key + b"=custom command\n", True, bind=True)

    def test_reports_match_the_auditioned_patterns(self):
        for cycles in (120, 350):
            report = probe.pulse_report(cycles)
            self.assertEqual(len(report), 65)
            self.assertEqual(report[0], 0)
            self.assertEqual(
                struct.unpack("<BBBHHHB", report[1:11]), (0x8F, 8, 2, 500, 500, cycles, 6)
            )
            self.assertEqual(report[11:], bytes(54))
        with self.assertRaises(ValueError):
            probe.pulse_report(65535)

    def test_shutdown_never_opens_hardware(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(probe.syslog, "openlog"),
            patch.object(probe.syslog, "syslog"),
            patch.object(probe, "local_controller") as find,
        ):
            probe.event("disconnect")
            find.assert_not_called()

    def test_event_patterns_send_two_reports_then_close(self):
        for kind in probe.PATTERNS:
            with (
                patch.object(probe.syslog, "openlog"),
                patch.object(probe.syslog, "syslog"),
                patch.object(Path, "exists", return_value=False),
                patch.object(Path, "resolve", return_value=Path("/driver/usbhid")),
                patch.object(
                    probe,
                    "local_controller",
                    return_value=(Path("/dev/hidraw9"), Path("/interface")),
                ),
                patch.object(probe.os, "open", return_value=99),
                patch.object(probe.os, "close") as close,
                patch.object(probe.time, "sleep") as sleep,
            ):
                reports = []

                def ioctl(fd, request, data, mutate):
                    if request == 0x80084803:
                        data[:] = struct.pack("=IHH", 3, 0x28DE, 0x1205)
                        return 0
                    reports.append(bytes(data))
                    return len(data)

                with patch.object(probe.fcntl, "ioctl", side_effect=ioctl):
                    probe.event(kind)
                cycles, gap = probe.PATTERNS[kind]
                self.assertEqual(reports, [probe.pulse_report(cycles)] * 2)
                self.assertEqual(
                    [c.args[0] for c in sleep.call_args_list], [cycles / 1000 + gap, cycles / 1000]
                )
                close.assert_called_once_with(99)


if __name__ == "__main__":
    unittest.main()
