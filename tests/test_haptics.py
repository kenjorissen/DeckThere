"""Production hooks, preference boundary and HID patterns without real hardware."""

import os
import struct
import subprocess
import sys
import tempfile
import unittest
from itertools import product
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
            run.return_value.stdout = "strong fanfare\n"
            self.assertEqual(h.preference(), "strong fanfare")
            run.return_value.stdout = "strong unknown"
            self.assertEqual(h.preference(), "off")
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
            event.assert_called_once_with("bind", "quiet", "buzzes")
            with patch.object(
                sys, "argv", ["deckthere_haptics.py", "--preview", "quiet", "--pattern", "fanfare"]
            ):
                h.main()
                event.assert_called_with("bind", "quiet", "fanfare")
            event.return_value = False
            with self.assertRaises(SystemExit) as exit_status:
                h.main()
            self.assertEqual(exit_status.exception.code, 2)

    def test_pattern_preference_defaults_and_preserves_existing_strength(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "haptics-pattern"
            strength = path.with_name("haptics")
            p.save_haptics(strength, False, "strong")
            self.assertEqual(p.read_haptic_pattern(path), "buzzes")
            for pattern in h.PATTERN_NAMES:
                p.save_haptic_pattern(path, pattern)
                self.assertEqual(p.read_haptic_pattern(path), pattern)
                self.assertEqual(p.read_haptics(strength), (False, "strong"))
            for raw in (b"invalid", b"x" * 100, b"\xff", b"buzzes" + b" " * 100):
                path.write_bytes(raw)
                self.assertEqual(p.read_haptic_pattern(path), "buzzes")
            path.unlink()
            os.mkfifo(path)
            self.assertEqual(p.read_haptic_pattern(path), "buzzes")
            path.unlink()
            path.symlink_to(strength)
            self.assertEqual(p.read_haptic_pattern(path), "buzzes")
            p.save_haptic_pattern(path, "fanfare")
            self.assertFalse(path.is_symlink())
            self.assertEqual(p.read_haptics(strength), (False, "strong"))
            with self.assertRaises(ValueError):
                p.save_haptic_pattern(path, "unknown")

    def test_musical_sequences_are_bounded_and_have_distinct_pitch_shapes(self):
        self.assertEqual(h.pattern_steps("bind", "buzzes"), ((500, 120, 0.15), (500, 120, 0)))
        for kind, count in (("bind", 6), ("disconnect", 5)):
            steps = h.pattern_steps(kind, "fanfare")
            self.assertEqual(len(steps), count)
            duration = sum(half * 2 * cycles / 1_000_000 + gap for half, cycles, gap in steps)
            self.assertLess(duration, 0.9)
            for strength in h.GAINS:
                for half, cycles, gap in steps:
                    report = h.pulse_report(cycles, strength, half)
                    self.assertEqual(len(report), 65)
                    self.assertEqual(struct.unpack_from("<HHH", report, 4), (half, half, cycles))
            self.assertEqual(steps[-1][2], 0)
        down = h.pattern_steps("disconnect", "fanfare")
        self.assertEqual([s[0] for s in down], sorted(s[0] for s in down))

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

    def test_events_use_saved_pattern_and_cancel_if_ownership_changes(self):
        for kind in h.PATTERNS:
            for pattern, lost in product(h.PATTERN_NAMES, (False, True)):
                with (
                    patch.object(Path, "exists", return_value=False),
                    patch.object(
                        Path,
                        "resolve",
                        side_effect=[Path("/driver/usbhid")]
                        + [Path("/driver/usbfs" if lost else "/driver/usbhid")] * 6,
                    ),
                    patch.object(h, "preference", return_value=f"normal {pattern}"),
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
                        self.assertEqual(h.event(kind), not lost)
                    steps = h.pattern_steps(kind, pattern)
                    if lost:
                        steps = steps[:1]
                    self.assertEqual(
                        reports,
                        [h.pulse_report(cycles, "normal", half) for half, cycles, gap in steps],
                    )
                    self.assertEqual(
                        [c.args[0] for c in sleep.call_args_list],
                        [cycles * 2 * half / 1_000_000 + gap for half, cycles, gap in steps],
                    )
                    close.assert_called_once_with(99)


if __name__ == "__main__":
    unittest.main()
