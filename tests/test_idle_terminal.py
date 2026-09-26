"""Run the real terminal loop with only temporary, unprivileged boundaries."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TerminalIdleTests(unittest.TestCase):
    def exercise(self, failure=None):
        with tempfile.TemporaryDirectory(prefix="deckthere idle ") as directory:
            base = Path(directory)
            calls = base / "calls"
            helper = base / "helper"
            helper.write_text(
                '#!/bin/bash\nprintf "helper:%s\\n" "$1" >>"$CALLS"\n'
                "if [[ $FAILURE == refused && $1 == start ]]; then exit 1; fi\n"
                "if [[ $FAILURE == stopping && $1 == keepalive ]]; then exit 2; fi\nexit 0\n"
            )
            (base / "sudo").write_text('#!/bin/bash\nshift\nexec "$@"\n')
            systemctl = base / "systemctl"
            systemctl.write_text(
                '#!/bin/bash\n[[ $1 == is-active && ! -e "$ONCE" ]] || exit 1\ntouch "$ONCE"\n'
            )
            for name in ("sleep", "ip", "ss"):
                (base / name).write_text(
                    "#!/bin/bash\nexit 1\n" if name != "sleep" else "#!/bin/bash\nexit 0\n"
                )
            for name in ("helper", "sudo", "systemctl", "sleep", "ip", "ss"):
                (base / name).chmod(0o755)
            (base / "deckthere_idle.py").write_text(
                "import os, sys\nfrom pathlib import Path\n"
                "action = sys.argv[1]\n"
                "with Path(os.environ['CALLS']).open('a') as stream: stream.write('idle:' + action + '\\n')\n"
                "if os.environ['FAILURE'] == action: raise SystemExit(1)\n"
                "if action == 'start': print('opaque-target')\n"
                "else: assert sys.argv[2:] == ['opaque-target']\n"
            )
            script = (ROOT / "src/deckthere.sh").read_text()
            script = script.replace(
                "HELPER=/home/.deckthere/bin/deckthere-root", f"HELPER={shlex.quote(str(helper))}"
            )
            script = script.replace("/usr/bin/systemctl", shlex.quote(str(systemctl)))
            script = script.replace(
                "POWER_SUPPLY_ROOT=/sys/class/power_supply",
                f"POWER_SUPPLY_ROOT={shlex.quote(str(base / 'absent'))}",
            )
            script = script.replace("next_idle_pulse=$((SECONDS + 10))", "next_idle_pulse=0")
            launcher = base / "deckthere.sh"
            launcher.write_text(script)
            result = subprocess.run(
                ["bash", str(launcher)],
                env=dict(
                    os.environ,
                    PATH=f"{base}:" + os.environ["PATH"],
                    CALLS=str(calls),
                    ONCE=str(base / "once"),
                    FAILURE=failure or "",
                ),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result, calls.read_text().splitlines()

    def test_start_pulse_loop_and_stop_order(self):
        result, calls = self.exercise()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls, ["idle:start", "helper:start", "helper:keepalive", "idle:pulse", "helper:stop"]
        )

    def test_idle_start_failure_never_starts_or_stops_service(self):
        result, calls = self.exercise("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["idle:start"])

    def test_refused_service_start_never_stops_other_session(self):
        result, calls = self.exercise("refused")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["idle:start", "helper:start"])

    def test_idle_loss_stops_owned_service(self):
        result, calls = self.exercise("pulse")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            calls, ["idle:start", "helper:start", "helper:keepalive", "idle:pulse", "helper:stop"]
        )

    def test_stopping_notification_does_not_publish_more_activity(self):
        result, calls = self.exercise("stopping")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["idle:start", "helper:start", "helper:keepalive", "helper:stop"])


if __name__ == "__main__":
    unittest.main()
