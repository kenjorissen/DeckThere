"""Brightness drift handling with temporary files, never real backlight hardware."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_display import functions  # noqa: E402

from deckthere_hardware import Brightness, brightness_target  # noqa: E402


class TerminalWatchTests(unittest.TestCase):
    def run_script(self, script):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / "brightness"
            path.write_text("6\n")
            result = subprocess.run(
                [
                    "bash",
                    "-euc",
                    functions("deckthere-root", "BRIGHTNESS_FUNCTIONS").replace(
                        "/run/deckthere/stopping", '"$STOPPING"'
                    )
                    + "\nbrightness=73; brightness_target_raw=6; next_brightness_notice=0;\n"
                    + script,
                ],
                env=dict(
                    os.environ,
                    BRIGHTNESS_FILE=str(path),
                    STOPPING=str(folder / "stop"),
                    CALLS=str(folder / "calls"),
                ),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result

    def test_no_redundant_writes_and_rate_limited_drift_notices(self):
        result = self.run_script("""
printf() { echo write >>"$CALLS"; builtin printf "$@"; }
maintain_brightness 0
[[ ! -e "$CALLS" ]]
for now in 1 2 31; do
  echo 900 >"$BRIGHTNESS_FILE"
  maintain_brightness "$now"
  [[ $(<"$BRIGHTNESS_FILE") == 6 ]]
done
[[ $(wc -l <"$CALLS") == 3 ]]
[[ $brightness == 73 ]]
""")
        self.assertEqual(result.stdout.count("Backlight changed externally"), 2)
        self.assertEqual(result.stderr, "")

    def test_shutdown_and_absent_target_do_not_write(self):
        result = self.run_script("""
printf() { exit 99; }
echo 73 >"$BRIGHTNESS_FILE"
touch "$STOPPING"
maintain_brightness 1
[[ $(<"$BRIGHTNESS_FILE") == 73 ]]
rm "$STOPPING"
brightness_target_raw=''
maintain_brightness 2
[[ $(<"$BRIGHTNESS_FILE") == 73 ]]
""")
        self.assertEqual(result.stdout, "")

    def test_read_write_and_invalid_value_errors_do_not_stop_service(self):
        result = self.run_script("""
rm "$BRIGHTNESS_FILE"
maintain_brightness 1
maintain_brightness 2
echo invalid >"$BRIGHTNESS_FILE"
maintain_brightness 31
echo 900 >"$BRIGHTNESS_FILE"
printf() { return 1; }
maintain_brightness 61
""")
        self.assertEqual(result.stdout.count("WARNING: brightness watcher"), 3)
        self.assertEqual(result.stderr, "")


class BrightnessWatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        # Deliberately avoid __init__, which discovers real fixed system paths.
        self.brightness = Brightness.__new__(Brightness)
        self.brightness.path = folder / "brightness"
        self.brightness.preference = folder / "preference"
        self.brightness.stopping = folder / "stopping"
        self.brightness.maximum = 1000
        self.brightness.product = "test"
        self.brightness.percent = 10
        self.brightness.dirty = False
        self.brightness.next_check = self.brightness.next_notice = 0.0
        self.brightness.path.write_text(str(self.target()))

    def target(self):
        return brightness_target(1000, self.brightness.percent, "test")

    def test_matching_brightness_is_not_rewritten(self):
        with patch.object(Path, "write_text", side_effect=AssertionError("redundant write")):
            self.assertIsNone(self.brightness.maintain(10))
        self.assertFalse(self.brightness.dirty)

    def test_external_change_is_corrected_without_changing_or_saving_selection(self):
        self.brightness.path.write_text("900")
        self.assertIn("changed externally", self.brightness.maintain(10))
        self.assertEqual(int(self.brightness.path.read_text()), self.target())
        self.assertEqual(self.brightness.percent, 10)
        self.assertFalse(self.brightness.dirty)
        self.brightness.save()
        self.assertFalse(self.brightness.preference.exists())

    def test_volume_changes_authoritative_target_not_external_or_old_saved_value(self):
        self.brightness.preference.write_text("10\n")
        self.brightness.path.write_text("900")
        self.brightness.change(1)
        self.assertEqual(self.brightness.percent, 11)
        self.assertEqual(int(self.brightness.path.read_text()), self.target())
        self.brightness.path.write_text("800")
        self.brightness.maintain(10)
        self.assertEqual(int(self.brightness.path.read_text()), self.target())
        self.assertEqual(self.brightness.preference.read_text(), "10\n")
        self.brightness.change(1)
        self.assertEqual(self.brightness.percent, 12)
        self.brightness.path.write_text("700")
        self.brightness.maintain(11)
        self.assertEqual(int(self.brightness.path.read_text()), self.target())
        self.brightness.change(-1)
        self.assertEqual(self.brightness.percent, 11)
        self.brightness.save()
        self.assertEqual(self.brightness.preference.read_text(), "11\n")

    def test_reads_are_limited_to_once_per_second(self):
        self.brightness.maintain(10)
        with patch.object(Path, "read_text", side_effect=AssertionError("extra read")):
            for now in (10, 10.01, 10.5, 10.999):
                self.assertIsNone(self.brightness.maintain(now))
        self.brightness.path.write_text("900")
        self.brightness.maintain(11)
        self.assertEqual(int(self.brightness.path.read_text()), self.target())

    def test_notices_are_limited_but_each_mismatch_is_corrected(self):
        for now, logged in ((10, True), (11, False), (39, False), (40, True)):
            self.brightness.path.write_text("900")
            self.assertEqual(self.brightness.maintain(now) is not None, logged)
            self.assertEqual(int(self.brightness.path.read_text()), self.target())

    def test_read_write_and_invalid_value_errors_are_nonfatal_and_rate_limited(self):
        with patch.object(Path, "read_text", side_effect=OSError("unavailable")):
            self.assertIn("WARNING", self.brightness.maintain(10))
            self.assertIsNone(self.brightness.maintain(11))
        self.brightness.path.write_text("900")
        with patch.object(Path, "write_text", side_effect=OSError("read-only")):
            self.assertIn("WARNING", self.brightness.maintain(40))
        self.brightness.path.write_text("invalid")
        self.assertIn("invalid backlight", self.brightness.maintain(70))
        self.assertEqual(self.brightness.path.read_text(), "invalid")

    def test_shutdown_never_reapplies_target_or_changes_selected_percentage(self):
        self.brightness.stopping.touch()
        self.brightness.path.write_text("73")  # Original value restored by service.
        self.brightness.change(1)
        self.assertIsNone(self.brightness.maintain(10))
        self.assertEqual(self.brightness.percent, 10)
        self.assertEqual(self.brightness.path.read_text(), "73")

    def test_unavailable_backlight_is_not_accessed(self):
        self.brightness.maximum = 0
        with patch.object(Path, "read_text", side_effect=AssertionError("unavailable device")):
            self.assertIsNone(self.brightness.maintain(10))
