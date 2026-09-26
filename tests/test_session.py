"""The installed launcher is exercised with mocked privilege/process boundaries."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("session", ROOT / "src/deckthere_session.py")
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class SessionTests(unittest.TestCase):
    def setUp(self):
        guards = [
            patch.object(session.os, "geteuid", return_value=1000),
            patch.dict(session.os.environ, {"WAYLAND_DISPLAY": "wayland-test"}),
            patch.object(session.signal, "signal"),
            patch.object(session.time, "sleep"),
            patch.object(session.subprocess, "run", return_value=MagicMock(returncode=0)),
            patch.object(session.subprocess, "Popen"),
            patch.object(session.IdleKeepalive, "start"),
        ]
        self.mocks = [guard.start() for guard in guards]
        for guard in guards:
            self.addCleanup(guard.stop)
        self.run = self.mocks[4]
        self.popen = self.mocks[5]
        self.ui = self.popen.return_value
        self.ui.poll.return_value = 0
        self.ui.returncode = 0
        self.idle_start = self.mocks[6]
        self.idle = self.idle_start.return_value

    def test_normal_quit_stops_service(self):
        with patch.object(session, "helper", return_value=0) as helper:
            self.assertEqual(session.main(), 0)
        self.assertEqual([call.args[0] for call in helper.call_args_list], ["start-gui", "stop"])
        self.assertIn("--session", self.popen.call_args.args[0])
        self.assertIn("-I", self.popen.call_args.args[0])

    def test_explicit_keyboard_uses_keyboard_start(self):
        with patch.object(session, "helper", return_value=0) as helper:
            self.assertEqual(session.main(keyboard=True), 0)
        self.assertEqual(
            [call.args[0] for call in helper.call_args_list], ["start-keyboard", "stop"]
        )

    def test_declined_start_never_stops_somebody_elses_session(self):
        with patch.object(session, "helper", return_value=1) as helper:
            self.assertEqual(session.main(), 1)
        helper.assert_called_once_with("start-gui")
        self.popen.assert_not_called()

    def test_missing_qt_does_not_start_hardware(self):
        self.run.return_value.returncode = 1
        with patch.object(session, "helper") as helper, self.assertRaises(SystemExit):
            session.main()
        helper.assert_not_called()
        self.popen.assert_not_called()

    def test_ui_launch_failure_stops_service(self):
        self.popen.side_effect = OSError("cannot execute")
        with patch.object(session, "helper", return_value=0) as helper:
            with self.assertRaises(OSError):
                session.main()
        self.assertEqual([call.args[0] for call in helper.call_args_list], ["start-gui", "stop"])

    def test_failed_heartbeat_terminates_ui_and_stops_service(self):
        self.ui.poll.return_value = None
        with patch.object(session, "helper", side_effect=[0, 1, 0]) as helper:
            session.main()
        self.assertEqual(
            [call.args[0] for call in helper.call_args_list],
            ["start-gui", "keepalive", "stop"],
        )
        self.ui.terminate.assert_called_once()
        self.ui.wait.assert_called_once_with(timeout=3)

    def test_idle_failure_before_start_never_touches_hardware(self):
        self.idle_start.side_effect = session.IdleError("missing counter")
        with patch.object(session, "helper") as helper:
            self.assertEqual(session.main(), 1)
        helper.assert_not_called()
        self.popen.assert_not_called()

    def test_idle_loss_stops_owned_session_and_ui(self):
        self.ui.poll.return_value = None
        self.idle.tick.side_effect = session.IdleError("session changed")
        with patch.object(session, "helper", return_value=0) as helper:
            self.assertEqual(session.main(), 1)
        self.assertEqual(
            [call.args[0] for call in helper.call_args_list],
            ["start-gui", "keepalive", "stop"],
        )
        self.ui.terminate.assert_called_once()
        self.idle.tick.assert_called_once()

    def test_idle_pulse_precedes_brightness_start_and_uses_existing_loop(self):
        events = []
        self.idle_start.side_effect = lambda: (events.append("idle-start"), self.idle)[1]
        self.idle.tick.side_effect = lambda: events.append("idle-tick")
        self.ui.poll.side_effect = [None, 0, 0]
        with patch.object(
            session, "helper", side_effect=lambda action, **kw: (events.append(action), 0)[1]
        ):
            self.assertEqual(session.main(), 0)
        self.assertEqual(events, ["idle-start", "start-gui", "keepalive", "idle-tick", "stop"])

    def test_signal_during_idle_start_does_not_start_service(self):
        def interrupted_start():
            callback = session.signal.signal.call_args_list[0].args[1]
            callback(session.signal.SIGTERM, None)
            return self.idle

        self.idle_start.side_effect = interrupted_start
        with patch.object(session, "helper") as helper:
            self.assertEqual(session.main(), 0)
        helper.assert_not_called()
        self.popen.assert_not_called()

    def test_hung_ui_is_killed_before_stop(self):
        self.ui.poll.return_value = None
        self.ui.wait.side_effect = [subprocess.TimeoutExpired("ui", 3), 0]
        with patch.object(session, "helper", side_effect=[0, 2, 0]):
            session.main()
        self.ui.kill.assert_called_once()


class ModeTests(unittest.TestCase):
    def test_setup_defaults_to_gui_and_preserves_or_overrides_saved_choice(self):
        source = (ROOT / "setup.sh").read_text()
        block = source.split("# BEGIN MODE_SELECTION\n", 1)[1].split("# END MODE_SELECTION", 1)[0]
        for saved, explicit, expected in (
            (None, "", "gui"),
            ("gui", "", "gui"),
            ("keyboard", "", "keyboard"),
            ("terminal", "", "terminal"),
            ("invalid", "", "gui"),
            (None, "keyboard", "keyboard"),
            ("keyboard", "terminal", "terminal"),
            ("terminal", "keyboard", "keyboard"),
        ):
            with (
                self.subTest(saved=saved, explicit=explicit),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory)
                if saved is not None:
                    preference = home / ".local/share/deckthere/launch-mode"
                    preference.parent.mkdir(parents=True)
                    preference.write_text(saved + "\n")
                result = subprocess.run(
                    ["bash", "-euc", block + '\nprintf "%s\\n" "$mode"'],
                    env=dict(os.environ, HOME=str(home), mode=explicit),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_wrapper_defaults_to_gui_and_honors_saved_full_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "deckthere-launch.sh"
            launcher.write_text((ROOT / "src/deckthere-launch.sh").read_text())
            terminal = root / "deckthere-gui.sh"
            terminal.write_text("#!/bin/bash\necho terminal\n")
            terminal.chmod(0o755)
            (root / "deckthere_preferences.py").write_text(
                (ROOT / "src/deckthere_preferences.py").read_text()
            )
            (root / "deckthere_session.py").write_text(
                'import sys; print("keyboard" if "--keyboard" in sys.argv else "gui")\n'
            )
            for saved, arguments, expected in (
                (None, [], "gui"),
                ("terminal", [], "terminal"),
                ("keyboard", ["--gui"], "gui"),
                ("gui", ["--gui", "--keyboard"], "keyboard"),
                ("keyboard", [], "keyboard"),
                ("keyboard", ["--terminal"], "terminal"),
                ("terminal", ["--keyboard"], "keyboard"),
            ):
                with self.subTest(saved=saved, arguments=arguments):
                    if saved is not None:
                        (root / "launch-mode").write_text(saved + "\n")
                    result = subprocess.run(
                        ["bash", str(launcher), *arguments],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), expected)

    def test_fixed_root_start_modes_and_busy_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            systemctl = root / "systemctl"
            calls = root / "calls"
            systemctl.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$*" >>"$CALLS"\nif [[ $1 == is-active ]]; then exit "${ACTIVE:-1}"; fi\n'
            )
            systemctl.chmod(0o755)
            source = (ROOT / "src/deckthere-root").read_text()
            source = source.replace("[[ $EUID == 0 && $# == 1 ]]", "[[ $# == 1 ]]")
            source = source.replace("/usr/bin/systemctl", str(systemctl))
            source = source.replace("/run/deckthere-launch", str(root / "selection"))
            source = source.replace("install -d -o root -g root", "install -d")
            helper = root / "helper"
            helper.write_text(source)
            for action, expected in (
                ("start", "terminal"),
                ("start-gui", "gui"),
                ("start-keyboard", "keyboard"),
            ):
                result = subprocess.run(
                    ["bash", str(helper), action],
                    env=dict(os.environ, CALLS=str(calls)),
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / "selection/mode").read_text().strip(), expected)
            calls.unlink()
            result = subprocess.run(
                ["bash", str(helper), "start"],
                env=dict(os.environ, CALLS=str(calls), ACTIVE="0"),
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "selection/mode").read_text().strip(), "keyboard")
            self.assertEqual(
                calls.read_text().splitlines(), ["is-active --quiet deckthere.service"]
            )
