"""Exercise terminal settings controls without Qt, a display, sudo or hardware."""

import subprocess
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "src/deckthere.sh").read_text()
CONTROLS = SOURCE.split("open_settings() {", 1)[1].split("# Invoked by the EXIT trap", 1)[0]
CONTROLS = "open_settings() {" + CONTROLS
DASHBOARD = SOURCE.split("# BEGIN DASHBOARD_FUNCTIONS\n", 1)[1].split(
    "# END DASHBOARD_FUNCTIONS", 1
)[0]


class TerminalSettingsTests(unittest.TestCase):
    def test_click_and_local_s_open_only_the_settings_target(self):
        for value, rows, cols, expected in (
            (b"s", 24, 80, True),
            (b"S", 24, 80, True),
            (b"\x1b[<0;40;1M", 24, 80, True),
            (b"\x1b[<0;35;1M", 24, 80, True),
            (b"\x1b[<0;46;1M", 24, 80, True),
            (b"\x1b[<0;20;10M", 20, 40, True),
            (b"\x1b[<0;1;1M", 24, 80, False),
            (b"\x1b[<0;40;2M", 24, 80, False),
            (b"\x1b[<0;40;1m", 24, 80, False),
            (b"\x1b[<1;40;1M", 24, 80, False),
            (b"\x1b[<0;99999999999;1M", 24, 80, False),
        ):
            with self.subTest(value=value):
                script = (
                    CONTROLS
                    + f"\nSLEEP_HELPER=/dev/null\nrows={rows}; cols={cols}\nopen_settings() {{ echo opened; }}\nterminal_input\n"
                )
                result = subprocess.run(
                    ["bash", "-euc", script], input=value, capture_output=True, timeout=2
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip() == b"opened", expected)

    def test_unavailable_controls_never_launch_a_process(self):
        result = subprocess.run(
            ["bash", "-euc", CONTROLS + "\nsettings_available=false; open_settings\n"],
            capture_output=True,
            timeout=2,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_button_is_replaced_by_actionable_notice_without_qt(self):
        for available in ("true", "false"):
            result = subprocess.run(
                [
                    "bash",
                    "-euc",
                    DASHBOARD
                    + f"\nrows=40; cols=100; settings_available={available}; paint_settings\n",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual("[ SETTINGS ]" in result.stdout, available == "true")
            self.assertEqual(
                "Settings unavailable - rerun setup with --gui" in result.stdout,
                available == "false",
            )


if __name__ == "__main__":
    unittest.main()
