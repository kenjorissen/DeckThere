"""Production hooks, preference boundary and HID patterns without real hardware."""

import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import deckthere_haptics as h  # noqa: E402
import deckthere_preferences as p  # noqa: E402


class HapticsTests(unittest.TestCase):
    def test_config_preserves_settings_is_idempotent_and_removable(self):
        original = b"License=private\r\nServerName=DeckThere\r\n"
        configured = h.configure(original)
        self.assertTrue(configured.startswith(original))
        self.assertIn(b"onBind.28de.1205=", configured)
        self.assertNotIn(b"onClientConnect=", configured)
        self.assertEqual(h.configure(configured), configured)
        self.assertEqual(h.configure(configured, remove=True), original)

    def test_probe_hooks_are_migrated(self):
        data = b"License=private\n"
        for key, event in (
            ("onClientConnect", "connect"),
            ("onBind.28de.1205", "bind"),
            ("onClientDisconnect", "disconnect"),
        ):
            data += f"{key}=/usr/bin/timeout 3s /usr/bin/python3 -I {h.PROBE} --event {event}\n".encode()
        configured = h.configure(data)
        self.assertNotIn(b"_probe.py", configured)
        self.assertNotIn(b"onClientConnect", configured)
        self.assertEqual(configured.count(b"onBind."), 1)

    def test_custom_hooks_are_not_overwritten_or_removed(self):
        for key in (
            "onBind",
            "onBind.28de",
            "onBind.28de.1205",
            "onClientDisconnect",
            "onClientConnect",
        ):
            data = f"{key}=custom command\n".encode()
            with self.assertRaises(ValueError):
                h.configure(data)
            self.assertEqual(h.configure(data, remove=True), data)

    def test_prepare_backs_up_once_and_preserves_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            original = b"License=secret\n"
            path.write_bytes(original)
            h.prepare(path)
            self.assertEqual(path.read_bytes(), h.configure(original))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            backup = path.with_name("config.ini.before-haptic-hooks")
            self.assertEqual(backup.read_bytes(), original)
            h.prepare(path)
            self.assertEqual(backup.read_bytes(), original)
            h.prepare(path, remove=True)
            self.assertEqual(path.read_bytes(), original)

    def test_prepare_rejects_symlinks_and_leaves_custom_config_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.symlink_to(Path(directory) / "missing")
            with self.assertRaises(OSError):
                h.prepare(path)
            path.unlink()
            original = b"onBind=custom\n"
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                h.prepare(path)
            self.assertEqual(path.read_bytes(), original)

    def test_settings_roundtrip_off_retains_strength_and_invalid_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "haptics"
            self.assertEqual(p.read_haptics(path), (True, "normal"))
            for enabled in (True, False):
                for strength in h.GAINS:
                    p.save_haptics(path, enabled, strength)
                    self.assertEqual(p.read_haptics(path), (enabled, strength))
            for bad in (b"1 insane", b"0", b"0 strong extra", b"\xff", b"x" * 100):
                path.write_bytes(bad)
                self.assertEqual(p.read_haptics(path), p.HAPTIC_DEFAULT)
            path.unlink()
            os.mkfifo(path)
            self.assertEqual(p.read_haptics(path), p.HAPTIC_DEFAULT)
            path.unlink()
            target = Path(directory) / "target"
            target.write_text("0 strong\n")
            path.symlink_to(target)
            self.assertEqual(p.read_haptics(path), p.HAPTIC_DEFAULT)
            p.save_haptics(path, True, "quiet")
            self.assertEqual(target.read_text(), "0 strong\n")
            self.assertFalse(path.is_symlink())
            with self.assertRaises(ValueError):
                p.save_haptics(path, 1, "normal")

    def test_preference_child_uses_fixed_command_and_fails_quiet(self):
        with patch.object(h.subprocess, "run") as run:
            run.return_value.stdout = "quiet\n"
            self.assertEqual(h.preference(), "quiet")
            self.assertIn("--installed-haptics", run.call_args.args[0])
            self.assertIn("-I", run.call_args.args[0])
            run.return_value.stdout = "invalid"
            self.assertEqual(h.preference(), "off")
            run.side_effect = subprocess.TimeoutExpired("test", 0.5)
            self.assertEqual(h.preference(), "off")

    def test_preview_runs_without_root_and_reports_unavailable(self):
        with (
            patch.object(sys, "argv", ["deckthere_haptics.py", "--preview", "quiet"]),
            patch.object(h.os, "geteuid", return_value=1000),
            patch.object(h.syslog, "openlog"),
            patch.object(h.signal, "signal"),
            patch.object(h.signal, "alarm"),
            patch.object(h, "event", return_value=True) as event,
        ):
            h.main()
            event.assert_called_once_with("bind", "quiet")
            event.return_value = False
            with self.assertRaises(SystemExit) as exit_status:
                h.main()
            self.assertEqual(exit_status.exception.code, 2)

    def test_gain_is_signed_and_patterns_are_bounded(self):
        for strength, gain in h.GAINS.items():
            for cycles in (120, 350):
                data = h.pulse_report(cycles, strength)
                self.assertEqual(len(data), 65)
                self.assertEqual(
                    struct.unpack("<BBBHHHb", data[1:11]), (0x8F, 8, 2, 500, 500, cycles, gain)
                )
        with self.assertRaises(ValueError):
            h.pulse_report(65535, "strong")

    def test_off_and_shutdown_do_not_discover_or_open_hardware(self):
        for stopping in (False, True):
            with (
                patch.object(Path, "exists", return_value=stopping),
                patch.object(h, "preference", return_value="off"),
                patch.object(h, "local_controller") as discover,
            ):
                h.event("bind")
                discover.assert_not_called()

    def test_events_send_two_reports_and_cancel_if_ownership_changes(self):
        for kind in h.PATTERNS:
            for lost in (False, True):
                with (
                    patch.object(Path, "exists", return_value=False),
                    patch.object(
                        Path,
                        "resolve",
                        side_effect=[
                            Path("/driver/usbhid"),
                            Path("/driver/usbfs" if lost else "/driver/usbhid"),
                        ],
                    ),
                    patch.object(h, "preference", return_value="normal"),
                    patch.object(
                        h,
                        "local_controller",
                        return_value=(Path("/dev/hidraw9"), Path("/interface")),
                    ),
                    patch.object(h.os, "open", return_value=99),
                    patch.object(h.os, "close") as close,
                    patch.object(h.syslog, "syslog"),
                    patch.object(h.time, "sleep") as sleep,
                ):
                    reports = []

                    def ioctl(fd, request, data, mutate):
                        if request == 0x80084803:
                            data[:] = struct.pack("=IHH", 3, 0x28DE, 0x1205)
                            return 0
                        reports.append(bytes(data))
                        return len(data)

                    with patch.object(h.fcntl, "ioctl", side_effect=ioctl):
                        h.event(kind)
                    cycles, gap = h.PATTERNS[kind]
                    self.assertEqual(
                        reports, [h.pulse_report(cycles, "normal")] * (1 if lost else 2)
                    )
                    self.assertEqual(sleep.call_args_list[0].args, (cycles / 1000 + gap,))
                    close.assert_called_once_with(99)


if __name__ == "__main__":
    unittest.main()
