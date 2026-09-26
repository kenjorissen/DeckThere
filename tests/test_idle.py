"""Idle integration tests: no real X server, Steam, input or root service calls."""

import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
import deckthere_idle as idle  # noqa: E402


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.value = 8
        self.available = {":0"}
        self.calls = []
        patches = [
            patch.dict(
                os.environ, {"XDG_CURRENT_DESKTOP": "gamescope", "DISPLAY": ":1"}, clear=True
            ),
            patch.object(idle.os, "getuid", return_value=1000),
            patch.object(idle.os, "geteuid", return_value=1000),
            patch.object(idle, "command", side_effect=self.command),
            patch.object(idle, "process_identity", return_value=321),
            patch.object(idle, "displays", return_value=[":0", ":1"]),
            patch.object(idle.time, "monotonic", return_value=100.0),
        ]
        self.mocks = [item.start() for item in patches]
        for item in patches:
            self.addCleanup(item.stop)
        # Tests capture startup notices without changing application logging.
        self.stderr = io.StringIO()
        guard = contextlib.redirect_stderr(self.stderr)
        guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)

    def command(self, arguments, display=None):
        self.calls.append((arguments, display))
        if arguments[0] == "/usr/bin/pgrep":
            return "42\n"
        if display not in self.available:
            return f"{idle.ATOM}: no such atom on any window.\n"
        if "-set" in arguments:
            self.value = int(arguments[-1])
            return ""
        return f"{idle.ATOM}(CARDINAL) = {self.value}\n"

    def writes(self):
        return [args for args, _display in self.calls if "-set" in args]

    def test_start_targets_steam_root_not_game_display_and_ticks_quietly(self):
        keeper = idle.IdleKeepalive.start()
        self.assertEqual(keeper.target.display, ":0")
        self.assertEqual(self.value, 9)
        before = len(self.calls)
        keeper.tick(109.99)
        self.assertEqual(len(self.calls), before)
        keeper.tick(110)
        self.assertEqual(self.value, 10)
        self.assertIn("saved power settings unchanged", self.stderr.getvalue())
        self.assertTrue(
            all(args[0] in ("/usr/bin/xprop", "/usr/bin/pgrep") for args, _ in self.calls)
        )

    def test_real_input_is_read_fresh_and_counter_is_not_restored_on_exit(self):
        keeper = idle.IdleKeepalive.start()
        self.value = 50
        keeper.tick(110)
        self.assertEqual(self.value, 51)
        # No cleanup writes, stale restoration, service or settings commands.
        before = list(self.calls)
        del keeper
        self.assertEqual(self.calls, before)

    def test_wraparound(self):
        self.value = 0xFFFFFFFF
        idle.IdleKeepalive.start()
        self.assertEqual(self.value, 0)

    def test_desktop_and_explicit_opt_out_never_touch_x11(self):
        for environment in ({"XDG_CURRENT_DESKTOP": "KDE"}, {idle.DISABLE_VARIABLE: "1"}):
            with patch.dict(os.environ, environment):
                keeper = idle.IdleKeepalive.start()
                keeper.tick()
                self.assertIsNone(keeper.target)
        self.assertFalse(self.calls)

    def test_root_is_rejected_before_any_commands(self):
        with patch.object(idle.os, "geteuid", return_value=0), self.assertRaises(idle.IdleError):
            idle.IdleKeepalive.start()
        self.assertFalse(self.calls)

    def test_absent_and_ambiguous_targets_never_create_property(self):
        for available in (set(), {":0", ":1"}):
            self.available = available
            with self.subTest(available=available), self.assertRaises(idle.IdleError):
                idle.IdleKeepalive.start()
        self.assertFalse(self.writes())

    def test_changed_compositor_never_writes(self):
        target = idle.Target(":0", 42, 123)
        with self.assertRaises(idle.IdleError):
            target.pulse()
        self.assertFalse(self.calls)

    def test_malformed_or_missing_counter_never_writes(self):
        for value in (-1, 0x100000000, "5, 6", "garbage"):
            self.value = value
            with self.subTest(value=value), self.assertRaises(idle.IdleError):
                idle.Target(":0", 42, 321).pulse()
        self.assertFalse(self.writes())
        with patch.object(idle, "command", return_value=f'{idle.ATOM}(STRING) = "7"\n'):
            with self.assertRaises(idle.IdleError):
                idle.Target(":0", 42, 321).pulse()

    def test_compositor_identification_requires_one_process(self):
        for value in ("", "42\n43\n", "not-a-pid"):
            with (
                patch.object(idle, "command", return_value=value),
                self.assertRaises(idle.IdleError),
            ):
                idle.compositor()

    def test_target_token_is_bounded_and_local(self):
        target = idle.Target(":0", 42, 321)
        self.assertEqual(idle.Target.parse(target.token()), target)
        for value in (
            '["host:0",42,321]',
            '[":0;bad",42,321]',
            '[":0",true,321]',
            '[":0",0,321]',
            '[":0",42,-1]',
            '[":0",42]',
            "{}",
            "[",
            "x" * 129,
        ):
            with self.subTest(value=value), self.assertRaises(idle.IdleError):
                idle.Target.parse(value)

    def test_cli_start_and_pulse(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(idle.main(["start"]), 0)
        token = output.getvalue().strip()
        self.assertEqual(idle.main(["pulse", token]), 0)
        self.assertEqual(self.value, 10)
        self.assertEqual(idle.main(["pulse", "unsafe"]), 1)
        self.assertEqual(idle.main(["unexpected"]), 1)


class BoundaryTests(unittest.TestCase):
    def test_commands_are_bounded_local_argv_with_c_locale_and_no_stdin(self):
        with patch.object(
            idle.subprocess, "run", return_value=MagicMock(returncode=0, stdout="ok")
        ) as run:
            self.assertEqual(idle.command(["/usr/bin/xprop", "-root"], ":0"), "ok")
        self.assertEqual(run.call_args.kwargs["timeout"], 1)
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(run.call_args.kwargs["env"]["DISPLAY"], ":0")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_command_errors_are_normalized_without_leaking_output(self):
        for error in (
            FileNotFoundError(),
            subprocess.TimeoutExpired("xprop", 1),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid output"),
        ):
            with (
                patch.object(idle.subprocess, "run", side_effect=error),
                self.assertRaises(idle.IdleError),
            ):
                idle.command(["/usr/bin/xprop"])
        with (
            patch.object(idle.subprocess, "run", return_value=MagicMock(returncode=1)),
            self.assertRaises(idle.IdleError),
        ):
            idle.command(["/usr/bin/xprop"])

    def test_discovery_ignores_remote_display_and_invalid_socket_names(self):
        entries = []
        for name in ("X1", "X0", "other", "Y2", "X-1", "X9999", "Xhost:0", "X2", "X3", "X4"):
            entry = MagicMock()
            entry.name = name
            entry.stat.return_value.st_uid = 1001 if name == "X2" else 1000
            entry.stat.return_value.st_mode = stat.S_IFLNK if name == "X3" else stat.S_IFSOCK
            if name == "X4":
                entry.stat.side_effect = FileNotFoundError()
            entries.append(entry)
        with (
            patch.object(idle.Path, "iterdir", return_value=iter(entries)),
            patch.object(idle.os, "getuid", return_value=1000),
            patch.dict(os.environ, {"DISPLAY": "remote:9"}),
        ):
            self.assertEqual(idle.displays(), [":0", ":1"])

    def test_process_identity_checks_uid_argv_and_start_time_without_exe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "42"
            proc.mkdir()
            (proc / "cmdline").write_bytes(b"gamescope\0--some-flag\0")
            (proc / "status").write_text("Name:\tgamescope-wl\nUid:\t1000\t1000\t1000\t1000\n")
            (proc / "stat").write_text(
                "42 (gamescope-wl) " + " ".join(["S"] + ["0"] * 18 + ["321"])
            )
            with (
                patch.object(
                    idle,
                    "Path",
                    side_effect=lambda value: root if value == "/proc" else Path(value),
                ),
                patch.object(idle.os, "getuid", return_value=1000),
            ):
                self.assertEqual(idle.process_identity(42), 321)
                (proc / "status").write_text("Uid:\t0\t0\t0\t0\n")
                with self.assertRaises(idle.IdleError):
                    idle.process_identity(42)
                (proc / "cmdline").write_bytes(b"unrelated\0")
                with self.assertRaises(idle.IdleError):
                    idle.process_identity(42)


if __name__ == "__main__":
    unittest.main()
