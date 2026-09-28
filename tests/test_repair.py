"""Repair tests use temporary files and mocked commands, never real privilege or UI."""

import fcntl
import importlib.util
import os
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repair = load("deckthere_repair")
gate = load("deckthere_launch_check")


class TrustTests(unittest.TestCase):
    def test_only_root_owned_regular_nonwritable_files_are_trusted(self):
        path = Mock()
        for mode, uid, okay in (
            (stat.S_IFREG | 0o644, 0, True),
            (stat.S_IFREG | 0o755, 0, True),
            (stat.S_IFREG | 0o664, 0, False),
            (stat.S_IFREG | 0o644, 1000, False),
            (stat.S_IFLNK | 0o777, 0, False),
            (stat.S_IFIFO | 0o600, 0, False),
        ):
            with self.subTest(mode=mode, uid=uid):
                path.lstat.return_value = SimpleNamespace(st_mode=mode, st_uid=uid)
                if okay:
                    repair.safe_stat(path)
                else:
                    with self.assertRaises(repair.RepairError):
                        repair.safe_stat(path)

    def test_all_ancestors_are_validated(self):
        with patch.object(repair, "safe_stat") as check:
            repair.parents(Path("/home/.deckthere/bin/deckthere.service"))
        self.assertEqual(
            [c.args[0] for c in check.call_args_list],
            [
                Path("/"),
                Path("/home"),
                Path("/home/.deckthere"),
                Path("/home/.deckthere/bin"),
            ],
        )
        self.assertTrue(all(c.kwargs == {"directory": True} for c in check.call_args_list))

    def test_subprocesses_are_bounded_and_do_not_inherit_root_environment(self):
        with patch.object(repair.subprocess, "run") as run:
            repair.command(["/usr/bin/systemctl", "--system", "daemon-reload"])
        self.assertEqual(run.call_args.kwargs["env"], repair.ENV)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)


class RepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.unit = self.root / "system/deckthere.service"
        self.rule = self.root / "sudoers/zz-deckthere"
        self.lock = self.root / "run/launch/lock"
        for directory in (self.unit.parent, self.rule.parent, self.lock.parent.parent):
            directory.mkdir(parents=True)
        for name in repair.FILES:
            (self.bin / name).write_bytes(b"fixture\n")
            (self.bin / name).chmod(
                0o700
                if name in ("deckthere-root", "deckthere_repair.py", "vhusbdx86_64")
                else 0o600
            )
        (self.bin / "owner-uid").write_text("1000\n")
        self.wanted = {
            self.unit: (b"[Unit]\nDescription=DeckThere\n", 0o644),
            self.rule: (b"fixture exact rule\n", 0o440),
        }
        (self.bin / "deckthere.service").write_bytes(self.wanted[self.unit][0])
        (self.bin / "deckthere.sudoers").write_bytes(self.wanted[self.rule][0])
        self.state = dict(
            LoadState="loaded",
            ActiveState="inactive",
            FragmentPath=str(self.unit),
            DropInPaths="",
            NeedDaemonReload="no",
        )
        self.commands = []
        self.failure = None
        real_fstat = os.fstat

        def fixture_fstat(fd):
            info = real_fstat(fd)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0)

        def fixture_stat(path, directory=False):
            # Simulate root ownership only; retain real file type/mode checks.
            self.assertTrue(path.is_relative_to(self.root))
            info = path.lstat()
            kind = stat.S_ISDIR if directory else stat.S_ISREG
            if not kind(info.st_mode) or info.st_mode & 0o022:
                raise repair.RepairError("unsafe fixture")
            return info

        guards = [
            patch.object(repair, "BIN", self.bin),
            patch.object(repair, "UNIT", self.unit),
            patch.object(repair, "RULE", self.rule),
            patch.object(repair, "LOCK", self.lock),
            patch.object(
                repair,
                "parents",
                side_effect=lambda p: self.assertTrue(p.is_relative_to(self.root)),
            ),
            patch.object(repair, "safe_stat", side_effect=fixture_stat),
            patch.object(repair.os, "fstat", side_effect=fixture_fstat),
            patch.object(repair.os, "geteuid", return_value=0),
            patch.object(repair, "command", side_effect=self.command),
            patch.dict(os.environ, {}, clear=True),
        ]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)

    def command(self, args):
        self.commands.append(args)
        self.assertIn(args[0], ("/usr/bin/systemctl", "/usr/sbin/visudo"))
        if "show" in args:
            return SimpleNamespace(
                returncode=0, stdout="\n".join(f"{k}={v}" for k, v in self.state.items())
            )
        if self.failure and self.failure(args):
            return SimpleNamespace(returncode=1, stdout="")
        if "daemon-reload" in args:
            self.state["LoadState"] = "loaded" if self.unit.exists() else "not-found"
            self.state["NeedDaemonReload"] = "no"
        return SimpleNamespace(returncode=0, stdout="")

    def install(self):
        for path, (data, mode) in self.wanted.items():
            path.write_bytes(data)
            path.chmod(mode)

    def test_missing_rule_is_restored_without_touching_settings_or_starting_sharing(self):
        self.install()
        self.rule.unlink()
        preferences = self.root / "preferences"
        preferences.write_bytes(b"unchanged user settings")
        unit_time = self.unit.stat().st_mtime_ns
        self.assertEqual(repair.main(["--repair"]), 0)
        self.assertEqual(repair.read_file(self.rule), self.wanted[self.rule])
        self.assertEqual(self.unit.stat().st_mtime_ns, unit_time)
        self.assertEqual(preferences.read_bytes(), b"unchanged user settings")
        self.assertFalse(any("start" in c or "stop" in c for c in self.commands))
        self.assertEqual(list(self.rule.parent.iterdir()), [self.rule])

    def test_missing_unit_and_rule_are_restored_then_reloaded(self):
        self.state["LoadState"] = "not-found"
        self.state["FragmentPath"] = ""
        # Emulate systemd learning the restored unit during reload.
        original = self.command

        def run(args):
            result = original(args)
            if "daemon-reload" in args:
                self.state["FragmentPath"] = str(self.unit)
            return result

        with patch.object(repair, "command", side_effect=run):
            self.assertEqual(repair.main(["--repair"]), 0)
        self.assertEqual(repair.read_file(self.unit), self.wanted[self.unit])
        self.assertEqual(repair.read_file(self.rule), self.wanted[self.rule])

    def test_check_is_read_only_and_reports_missing_integration(self):
        before = sorted(self.root.rglob("*"))
        self.assertEqual(repair.main(["--check"]), 1)
        self.assertEqual(sorted(self.root.rglob("*")), before)
        self.assertTrue(all("show" in c for c in self.commands))

    def test_healthy_active_check_succeeds_without_auth_or_writes(self):
        self.install()
        self.state["ActiveState"] = "active"
        self.assertEqual(repair.main(["--check"]), 0)
        self.assertFalse(self.lock.exists())

    def test_repair_refuses_active_or_transitioning_service(self):
        for state in ("active", "activating", "deactivating", "reloading"):
            self.state["ActiveState"] = state
            self.assertEqual(repair.main(["--repair"]), 2)
            self.assertFalse(self.rule.exists())
            self.assertFalse(self.unit.exists())

    def test_masked_foreign_or_overridden_unit_is_not_replaced(self):
        for key, value in (
            ("LoadState", "masked"),
            ("FragmentPath", "/other/service"),
            ("DropInPaths", "/custom/override.conf"),
        ):
            with self.subTest(key=key), patch.dict(self.state, {key: value}):
                self.assertEqual(repair.main(["--repair"]), 2)
                self.assertFalse(self.rule.exists())

    def test_invalid_template_never_publishes_and_cleans_staging(self):
        self.failure = lambda args: "-cf" in args
        self.assertEqual(repair.main(["--repair"]), 2)
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.rule.exists())
        self.assertEqual(list(self.unit.parent.iterdir()), [])
        self.assertEqual(list(self.rule.parent.iterdir()), [])

    def test_failed_global_validation_rolls_back_both_files(self):
        self.install()
        self.unit.write_bytes(b"old service\n")
        self.unit.chmod(0o640)
        self.rule.unlink()
        self.failure = lambda args: args == ["/usr/sbin/visudo", "-c"]
        self.assertEqual(repair.main(["--repair"]), 2)
        self.assertEqual(repair.read_file(self.unit), (b"old service\n", 0o640))
        self.assertFalse(self.rule.exists())
        self.assertEqual(list(self.rule.parent.iterdir()), [])

    def test_failed_reload_rolls_back(self):
        self.failure = lambda args: "daemon-reload" in args
        self.assertEqual(repair.main(["--repair"]), 2)
        self.assertFalse(self.rule.exists())
        self.assertFalse(self.unit.exists())

    def test_symlink_and_fifo_targets_are_never_followed(self):
        outside = self.root / "unrelated"
        outside.write_bytes(b"unchanged")
        self.rule.symlink_to(outside)
        self.assertEqual(repair.main(["--repair"]), 2)
        self.assertEqual(outside.read_bytes(), b"unchanged")
        self.rule.unlink()
        os.mkfifo(self.rule, 0o600)
        self.assertEqual(repair.main(["--repair"]), 2)
        self.assertFalse(self.unit.exists())

    def test_busy_start_lock_refuses_repair_without_waiting(self):
        self.lock.parent.mkdir()
        with self.lock.open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(repair.main(["--repair"]), 2)
        self.assertFalse(self.rule.exists())
        self.assertFalse(self.commands)

    def test_unsafe_or_missing_payload_never_changes_integration(self):
        source = self.bin / "deckthere.service"
        source.chmod(0o666)
        self.assertEqual(repair.main(["--repair"]), 2)
        source.unlink()
        self.assertEqual(repair.main(["--repair"]), 2)
        self.assertFalse(self.commands)
        self.assertFalse(self.rule.exists())

    def test_oversized_template_is_rejected_before_writes(self):
        (self.bin / "deckthere.service").write_bytes(b"x" * (repair.LIMIT + 1))
        self.assertEqual(repair.main(["--repair"]), 2)
        self.assertFalse(self.rule.exists())
        self.assertFalse(self.unit.exists())

    def test_second_publication_failure_rolls_back_first(self):
        replace = os.replace

        def fail_second(source, destination):
            if destination == self.rule:
                raise PermissionError("fixture")
            return replace(source, destination)

        with patch.object(repair.os, "replace", side_effect=fail_second):
            self.assertEqual(repair.main(["--repair"]), 2)
        self.assertEqual(list(self.unit.parent.iterdir()), [])
        self.assertEqual(list(self.rule.parent.iterdir()), [])

    def test_publication_defers_signals_and_restores_original_mask(self):
        self.install()
        with patch.object(repair.signal, "pthread_sigmask", return_value=set()) as mask:
            self.assertEqual(repair.main(["--repair"]), 0)
        self.assertEqual(
            mask.call_args_list[0].args,
            (repair.signal.SIG_BLOCK, {repair.signal.SIGINT, repair.signal.SIGTERM}),
        )
        self.assertEqual(mask.call_args_list[1].args, (repair.signal.SIG_SETMASK, set()))

    def test_different_pkexec_owner_and_arguments_are_rejected(self):
        with patch.dict(os.environ, {"PKEXEC_UID": "1001"}):
            self.assertEqual(repair.main(["--repair"]), 2)
        self.assertEqual(repair.main(["--repair", "/other/path"]), 2)
        self.assertFalse(self.commands)


class SourceInstallTests(unittest.TestCase):
    def test_real_install_block_preserves_exact_templates_without_checkout(self):
        setup = (ROOT / "setup.sh").read_text()
        block = setup.split("# BEGIN REPAIR_SOURCE_INSTALL\n", 1)[1].split(
            "# END REPAIR_SOURCE_INSTALL", 1
        )[0]
        rule_line = next(line for line in setup.splitlines() if line.startswith("printf '%s ALL="))
        self.assertNotIn("repair", rule_line)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed root bin"
            installed.mkdir()
            block = block.replace("sudo install -o root -g root", "install")
            block = block.replace("/home/.deckthere/bin", shlex.quote(str(installed)))
            result = subprocess.run(
                ["bash", "-ec", rule_line + "\n" + block],
                cwd=ROOT,
                env=dict(os.environ, tmp=str(root), user="fixture"),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (installed / "deckthere.service").read_bytes(),
                (ROOT / "packaging/deckthere.service").read_bytes(),
            )
            self.assertEqual(
                (installed / "deckthere.sudoers").read_bytes(), (root / "sudoers").read_bytes()
            )
            self.assertEqual(stat.S_IMODE((installed / "deckthere.sudoers").stat().st_mode), 0o600)
            actions = [
                item.split()[-1]
                for item in (root / "sudoers").read_text().split("NOPASSWD:")[1].split(",")
            ]
            self.assertEqual(
                actions, ["start", "start-gui", "start-keyboard", "stop", "keepalive", "check"]
            )


class AgentTests(unittest.TestCase):
    def test_existing_agent_is_not_started_or_stopped(self):
        with (
            patch.object(gate, "run", return_value=0),
            patch.object(gate.subprocess, "Popen") as spawn,
        ):
            with gate.authentication_agent():
                pass
        spawn.assert_not_called()

    def test_owned_agent_is_stopped_after_success_or_cancellation(self):
        for cancelled in (False, True):
            with (
                self.subTest(cancelled=cancelled),
                patch.object(gate, "run", return_value=1),
                patch.object(gate.subprocess, "Popen") as spawn,
            ):
                agent = spawn.return_value
                agent.poll.return_value = None
                try:
                    with gate.authentication_agent():
                        if cancelled:
                            raise gate.Cancelled()
                except gate.Cancelled:
                    pass
                self.assertEqual(spawn.call_args.args[0], [gate.AGENT])
                self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
                self.assertEqual(spawn.call_args.kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(spawn.call_args.kwargs["stderr"], subprocess.DEVNULL)
                agent.terminate.assert_called_once()
                agent.wait.assert_called_once_with(timeout=3)

    def test_hung_owned_agent_is_killed_with_bounded_wait(self):
        with (
            patch.object(gate, "run", return_value=1),
            patch.object(gate.subprocess, "Popen") as spawn,
        ):
            agent = spawn.return_value
            agent.poll.return_value = None
            agent.wait.side_effect = [subprocess.TimeoutExpired("agent", 3), None]
            with gate.authentication_agent():
                pass
            agent.kill.assert_called_once()
            self.assertEqual(agent.wait.call_count, 2)

    def test_agent_that_exited_is_not_signalled_again(self):
        with (
            patch.object(gate, "run", return_value=1),
            patch.object(gate.subprocess, "Popen") as spawn,
        ):
            spawn.return_value.poll.return_value = 1
            with gate.authentication_agent():
                pass
            spawn.return_value.terminate.assert_not_called()

    def test_inspection_failure_does_not_spawn_or_continue(self):
        with (
            patch.object(gate, "run", return_value=2),
            patch.object(gate.subprocess, "Popen") as spawn,
        ):
            with self.assertRaises(OSError), gate.authentication_agent():
                self.fail("Must not request authorization")
            spawn.assert_not_called()

    def test_native_process_environment_keeps_steam_context_not_library_injection(self):
        env = {
            "DISPLAY": ":1",
            "SteamAppId": "fixture",
            "DBUS_SESSION_BUS_ADDRESS": "fixture-bus",
            "LD_LIBRARY_PATH": "/untrusted",
            "LD_PRELOAD": "/untrusted.so",
            "PYTHONPATH": "/untrusted",
            "PYTHONHOME": "/untrusted",
            "LD_AUDIT": "/untrusted.so",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(gate.subprocess, "run") as run:
            gate.run([gate.DIALOG, "--yesno", "fixture"])
        self.assertEqual(
            run.call_args.kwargs["env"],
            {"DISPLAY": ":1", "SteamAppId": "fixture", "DBUS_SESSION_BUS_ADDRESS": "fixture-bus"},
        )


class GateTests(unittest.TestCase):
    def setUp(self):
        guards = [
            patch.object(gate.os, "geteuid", return_value=1000),
            patch.object(gate, "trusted", return_value=True),
            patch.object(gate, "graphical", return_value=True),
            patch.object(gate, "run", return_value=0),
            patch.object(gate, "message"),
            patch.object(gate, "authentication_agent"),
        ]
        self.mocks = [g.start() for g in guards]
        for guard in guards:
            self.addCleanup(guard.stop)
        self.run = self.mocks[3]
        self.message = self.mocks[4]

    def test_healthy_path_never_opens_dialog_or_requests_auth(self):
        self.assertEqual(gate.main(), 0)
        self.mocks[5].assert_not_called()
        self.run.assert_called_once_with(["/usr/bin/sudo", "-k", "-n", str(gate.HELPER), "check"])
        self.message.assert_not_called()

    def test_repair_uses_fixed_command_and_rechecks(self):
        self.run.side_effect = [1, 0, 0, 0, 0]
        self.assertEqual(gate.main(), 0)
        calls = [c.args[0] for c in self.run.call_args_list]
        self.assertIn("--yesno", calls[2])
        self.assertEqual(
            calls[3], ["/usr/bin/pkexec", "--disable-internal-agent", str(gate.REPAIR), "--repair"]
        )
        self.assertEqual(calls[0], calls[4])
        self.message.assert_not_called()

    def test_cancel_before_auth_and_during_auth_never_rechecks_or_starts(self):
        for results in ([1, 0, 1], [1, 0, 0, 126]):
            self.run.reset_mock()
            self.run.side_effect = results
            self.assertEqual(gate.main(), 1)
            self.assertEqual(self.run.call_count, len(results))
        self.message.assert_not_called()

    def test_failed_authorization_or_failed_postcheck_has_visible_fallback(self):
        for results in ([1, 0, 0, 127], [1, 0, 0, 0, 1]):
            self.run.reset_mock()
            self.run.side_effect = results
            self.assertEqual(gate.main(), 1)
            self.assertEqual(self.run.call_count, len(results))
            self.assertIn("Desktop Mode", self.message.call_args.args[0])

    def test_damaged_payload_does_not_request_auth(self):
        self.run.side_effect = [1, 2]
        self.assertEqual(gate.main(), 1)
        self.assertEqual(self.run.call_count, 2)
        self.message.assert_called_once()

    def test_timeout_fails_closed_without_retry(self):
        self.run.side_effect = subprocess.TimeoutExpired("fixture", 10)
        self.assertEqual(gate.main(), 1)
        self.assertEqual(self.run.call_count, 1)
        self.message.assert_called_once()

    def test_no_display_does_not_attempt_graphical_auth(self):
        self.mocks[2].return_value = False
        self.run.side_effect = [1, 0]
        self.assertEqual(gate.main(), 1)
        self.assertEqual(self.run.call_count, 2)

    def test_signal_cancellation_closes_agent_context_without_another_dialog(self):
        self.run.side_effect = [1, 0, gate.Cancelled()]
        self.assertEqual(gate.main(), 1)
        self.mocks[5].return_value.__exit__.assert_called_once()
        self.message.assert_not_called()

    def test_failed_gate_prevents_both_interfaces_in_real_launcher(self):
        for mode in ("--gui", "--terminal", "--keyboard"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                launcher = root / "deckthere-launch.sh"
                launcher.write_bytes((ROOT / "src/deckthere-launch.sh").read_bytes())
                (root / "deckthere_launch_check.py").write_text("raise SystemExit(1)\n")
                result = subprocess.run(
                    ["bash", str(launcher), mode],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")
