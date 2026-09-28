"""Session dimming with fake files only; no hardware, privileges or Steam access."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_backend import Harness  # noqa: E402

import deckthere_dashboard as dashboard  # noqa: E402
import deckthere_preferences as preferences  # noqa: E402
from deckthere_hardware import Brightness, brightness_target  # noqa: E402


class AutoDimTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.b = Brightness.__new__(Brightness)
        self.b.path = self.base / "brightness"
        self.b.path.write_text("730\n")
        self.b.preference = self.base / "percent"
        self.b.preference.write_text("10\n")
        self.b.restore_file = self.base / "restore"
        self.b.stopping = self.base / "stopping"
        self.b.maximum = 1000
        self.b.product = "test"
        self.b.original = None
        self.b.percent = 10
        self.b.dirty = False
        self.b.next_check = self.b.next_notice = 0.0
        self.b.refresh()

    def raw(self):
        return int(self.b.path.read_text())

    def target(self, percent):
        return brightness_target(1000, percent, "test")

    def test_constructor_reads_startup_state_without_writing_hardware(self):
        self.b.path.with_name("max_brightness").write_text("1000\n")
        product = self.base / "product"
        product.write_text("test\n")
        paths = {
            "/sys/class/backlight/amdgpu_bl0/brightness": self.b.path,
            "/home/.deckthere/data/brightness-percent": self.b.preference,
            "/run/deckthere/stopping": self.b.stopping,
            "/run/deckthere/brightness-restore": self.b.restore_file,
            "/sys/class/dmi/id/product_name": product,
        }
        for enabled in (True, False):
            if enabled:
                self.b.restore_file.write_text("730\n")
                self.b.path.write_text(str(self.target(10)))
            else:
                self.b.restore_file.unlink()
                self.b.path.write_text("730\n")
            with (
                patch("deckthere_hardware.Path", side_effect=lambda name: paths[name]),
                patch.object(Path, "write_text", side_effect=AssertionError("startup write")),
            ):
                constructed = Brightness()
            self.assertEqual(constructed.auto_dim, enabled)
            self.assertEqual(constructed.percent == 10, enabled)
            self.assertEqual(constructed.original, 730 if enabled else None)

    def test_off_never_enforces_or_saves_external_brightness(self):
        self.b.path.write_text("900")
        with patch.object(Path, "write_text", side_effect=AssertionError("unexpected write")):
            self.b.maintain(10)
            self.b.save()
        self.assertEqual(self.raw(), 900)
        self.assertEqual(self.b.preference.read_text(), "10\n")
        self.assertGreater(self.b.percent, 90)

    def test_manual_control_uses_current_level_not_old_saved_dim_level(self):
        self.b.path.write_text(str(self.target(70)))
        self.b.change(1)
        self.assertEqual(self.b.percent, 71)
        self.assertEqual(self.raw(), self.target(71))
        self.b.path.write_text(str(self.target(40)))
        self.b.change(-1)
        self.assertEqual(self.b.percent, 39)
        self.assertEqual(self.raw(), self.target(39))
        self.b.save()
        self.assertEqual(self.b.preference.read_text(), "39\n")
        self.assertFalse(self.b.restore_file.exists())

    def test_manual_keys_never_adjust_wrong_way_outside_oled_calibration(self):
        self.b.maximum = 599000
        self.b.product = "Galileo"
        self.b.path.write_text("599000")
        self.b.change(1)
        self.assertEqual(self.raw(), 599000)
        self.assertEqual(self.b.percent, 100)
        self.b.change(-1)
        self.assertLessEqual(self.raw(), 599000)
        self.b.path.write_text("500")
        self.b.change(-1)
        self.assertEqual(self.raw(), 500)
        self.b.change(1)
        self.assertGreaterEqual(self.raw(), 500)

    def test_external_change_before_release_does_not_get_saved_as_preference(self):
        self.b.change(1)
        requested = self.b.percent
        self.b.path.write_text("900")
        self.b.maintain(10)
        self.b.save()
        self.assertEqual(self.b.preference.read_text(), f"{requested}\n")
        self.assertNotEqual(self.b.percent, requested)

    def test_on_matches_startup_and_off_restores_once_without_changing_default(self):
        self.b.set_auto_dim(True)
        self.assertTrue(self.b.auto_dim)
        self.assertEqual(self.raw(), self.target(10))
        self.assertEqual(self.b.restore_file.read_text(), "730\n")
        self.assertEqual(self.b.restore_file.stat().st_mode & 0o777, 0o600)
        self.b.path.write_text("900")
        self.b.maintain(10)
        self.assertEqual(self.raw(), self.target(10))
        self.b.set_auto_dim(True)  # No new restoration point.
        self.assertEqual(self.b.original, 730)
        self.b.change(1)
        self.b.set_auto_dim(False)
        self.assertFalse(self.b.auto_dim)
        self.assertEqual(self.raw(), 730)
        self.assertFalse(self.b.restore_file.exists())
        self.b.path.write_text("800")
        self.b.set_auto_dim(False)
        self.b.maintain(20)
        self.assertEqual(self.raw(), 800)
        self.assertEqual(self.b.preference.read_text(), "11\n")
        self.assertFalse((self.base / "auto-dim").exists())
        self.b.set_auto_dim(True)
        self.assertEqual(self.b.original, 800)
        self.assertEqual(self.raw(), self.target(11))
        self.b.set_auto_dim(False)
        self.assertEqual(self.raw(), 800)

    def test_shutdown_blocks_toggle_manual_writes_and_enforcement(self):
        self.b.set_auto_dim(True)
        self.b.stopping.touch()
        self.b.path.write_text("730")
        for state in (True, False):
            with self.assertRaises(OSError):
                self.b.set_auto_dim(state)
        self.b.change(1)
        self.b.maintain(10)
        self.assertEqual(self.raw(), 730)

    def test_invalid_backlight_does_not_dim_or_create_restoration_state(self):
        for value in ("invalid", "1001", "-1"):
            self.b.path.write_text(value)
            with self.assertRaises(ValueError):
                self.b.set_auto_dim(True)
            self.b.change(1)
            self.assertEqual(self.b.path.read_text(), value)
            self.assertFalse(self.b.restore_file.exists())

    def test_failed_write_retains_restoration_point_for_service_cleanup(self):
        with patch.object(Path, "write_text", side_effect=OSError("mock write failure")):
            with self.assertRaises(OSError):
                self.b.set_auto_dim(True)
        self.assertTrue(self.b.auto_dim)
        self.assertEqual(self.b.restore_file.read_text(), "730\n")
        with patch.object(Path, "write_text", side_effect=OSError("mock restore failure")):
            with self.assertRaises(OSError):
                self.b.set_auto_dim(False)
        self.assertTrue(self.b.auto_dim)
        self.assertTrue(self.b.restore_file.exists())
        self.b.set_auto_dim(False)
        self.assertFalse(self.b.auto_dim)

    def test_backend_reports_confirmed_state_and_failures_do_not_stop_sharing(self):
        with Harness() as harness:
            client = harness.connect()
            self.addCleanup(client.close)
            self.assertTrue(client.wait_for("status")["auto_dim"])
            client.send({"op": "auto_dim", "enabled": False})
            self.assertFalse(client.wait_for("status")["auto_dim"])
            with patch.object(harness.brightness, "set_auto_dim", side_effect=OSError("mock")):
                client.send({"op": "auto_dim", "enabled": True})
                self.assertEqual(client.wait_for("brightness_error"), {"op": "brightness_error"})
                self.assertFalse(client.wait_for("status")["auto_dim"])
            self.assertFalse(harness.backend.stopping)


class PreferenceTests(unittest.TestCase):
    def test_bounded_boolean_defaults_atomic_save_and_unsafe_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / "auto-dim"
            self.assertTrue(preferences.read_auto_dim(path))
            for enabled in (False, True):
                preferences.save_auto_dim(path, enabled)
                self.assertEqual(preferences.read_auto_dim(path), enabled)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for value in (b"", b"0\n1", b"0 " * 100, b"\xff", b"false", b"$(id)"):
                path.write_bytes(value)
                self.assertTrue(preferences.read_auto_dim(path))
            path.write_text("0\n")
            link = base / "link"
            link.symlink_to(path)
            fifo = base / "fifo"
            os.mkfifo(fifo)
            for unsafe in (link, fifo, base):
                self.assertTrue(preferences.read_auto_dim(unsafe))
            preferences.save_auto_dim(link, True)
            self.assertEqual(path.read_text(), "0\n")
            self.assertFalse(link.is_symlink())
            with self.assertRaises(ValueError):
                preferences.save_auto_dim(path, 0)

    def test_root_reader_drops_all_privileges_before_user_path_access(self):
        calls = []
        owner = SimpleNamespace(pw_gid=1000, pw_dir="/home/fixture")
        with (
            patch.object(Path, "read_text", return_value="1000\n"),
            patch.object(preferences.pwd, "getpwuid", return_value=owner),
            patch.object(preferences.os, "setgroups", side_effect=lambda _: calls.append("groups")),
            patch.object(preferences.os, "setgid", side_effect=lambda _: calls.append("gid")),
            patch.object(preferences.os, "setuid", side_effect=lambda _: calls.append("uid")),
            patch.object(
                preferences, "read_auto_dim", side_effect=lambda _: calls.append("read") or False
            ),
        ):
            self.assertFalse(preferences.installed_auto_dim())
        self.assertEqual(calls, ["groups", "gid", "uid", "read"])

    def test_setup_flags_preserve_default_when_omitted_and_reject_conflicts(self):
        source = (
            (ROOT / "setup.sh")
            .read_text()
            .split("# BEGIN DOWNLOAD_OPTIONS\n")[1]
            .split("# END DOWNLOAD_OPTIONS")[0]
        )
        for args, expected in (
            ([], ""),
            (["--disable-auto-dim"], "0"),
            (["--enable-auto-dim"], "1"),
        ):
            result = subprocess.run(
                ["bash", "-euc", source + '\nprintf "%s" "$auto_dim"', "setup", *args],
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)
        result = subprocess.run(
            ["bash", "-euc", source, "setup", "--enable-auto-dim", "--disable-auto-dim"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_adaptive_warning_hides_only_for_one_explicit_off_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.vdf"
            self.assertTrue(dashboard.adaptive_warning(path))
            for data, expected in (
                (b'"AdaptiveBrightnessEnabled" "0"\n', False),
                (b'"AdaptiveBrightnessEnabled" "1"\n', True),
                (b'"DisplayBrightness" "0.75"\n', True),
                (b'"AdaptiveBrightnessEnabled" "0"\n"AdaptiveBrightnessEnabled" "1"\n', True),
                (b'// "AdaptiveBrightnessEnabled" "0"\n', True),
                (b'"AdaptiveBrightnessEnabled" "0"\n' + b" " * (4 * 1024 * 1024), True),
            ):
                path.write_bytes(data)
                self.assertEqual(dashboard.adaptive_warning(path), expected)
            path.unlink()
            os.mkfifo(path)
            self.assertTrue(dashboard.adaptive_warning(path))
            path.unlink()
            path.symlink_to(Path(directory))
            self.assertTrue(dashboard.adaptive_warning(path))
