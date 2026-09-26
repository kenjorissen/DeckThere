import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import vhp_preferences as preferences  # noqa: E402


class PreferencesTests(unittest.TestCase):
    def test_default_and_all_saved_modes_including_legacy_keyboard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launch-mode"
            self.assertEqual(preferences.read_mode(path), "gui")
            for mode in preferences.MODES:
                preferences.save_mode(path, mode)
                self.assertEqual(preferences.read_mode(path), mode)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["launch-mode"])

    def test_invalid_values_never_execute_and_fall_back_to_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mode"
            for data in (b"", b"$(exit 7)", b"gui" + b" " * 100, b"\xff", b"terminal\ngui"):
                path.write_bytes(data)
                self.assertEqual(preferences.read_mode(path), "gui")
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                preferences.save_mode(path, "unknown")
            self.assertEqual(path.read_bytes(), before)

    def test_symlink_fifo_and_directory_are_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            original = base / "original"
            original.write_text("terminal\n")
            link = base / "link"
            link.symlink_to(original)
            fifo = base / "fifo"
            os.mkfifo(fifo)
            for path in (base, link, fifo):
                self.assertEqual(preferences.read_mode(path), "gui")
            preferences.save_mode(link, "keyboard")
            self.assertEqual(original.read_text(), "terminal\n")
            self.assertFalse(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
