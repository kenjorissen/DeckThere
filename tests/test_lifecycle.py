"""Exercise the actual shell runner with temporary paths and a fake USB server."""

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LifecycleTests(unittest.TestCase):
    def test_launcher_sigkill_expires_lease_and_restores_brightness(self):
        self.exercise(kill_launcher=True)

    def test_service_term_restores_brightness(self):
        self.exercise(kill_launcher=False)

    def test_terminal_corrects_external_brightness_and_restores_original_on_exit(self):
        self.exercise(kill_launcher=False, brightness_reset=True)

    def test_touch_request_stops_service_and_restores_brightness(self):
        self.exercise(kill_launcher=False, touch_exit=True)

    def test_keyboard_ignores_corner_requests_and_does_not_start_touch_monitor(self):
        self.exercise(kill_launcher=False, keyboard=True, touch_exit=True)

    def test_keyboard_backend_exit_stops_server_and_restores_brightness(self):
        self.exercise(kill_launcher=False, keyboard_exit=True)

    def test_keyboard_launcher_sigkill_stops_backend_and_restores_brightness(self):
        self.exercise(kill_launcher=True, keyboard=True, stale_lease=True)

    def test_group_term_during_cleanup_still_restores_brightness(self):
        self.exercise(kill_launcher=False, keyboard_exit=True, stop_during_cleanup=True)

    def exercise(
        self,
        kill_launcher,
        touch_exit=False,
        keyboard=False,
        keyboard_exit=False,
        brightness_reset=False,
        stop_during_cleanup=False,
        stale_lease=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            runtime = folder / "runtime"
            runtime.mkdir()
            (runtime / "stopping").touch()  # A new run must clear stale state.
            brightness = folder / "brightness"
            brightness.write_text("73\n")
            brightness.chmod(0o640)
            (folder / "max_brightness").write_text("200\n")
            preference = folder / "brightness-percent"
            preference.write_text("10\n")
            server = folder / "server"
            # Ignore TERM to verify bounded shutdown and escalation too.
            server.write_text(
                "#!/usr/bin/env python3\nimport signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nwhile True: time.sleep(1)\n"
            )
            server.chmod(0o755)
            monitor = folder / "touch-stop.py"
            monitor_ready = folder / "monitor-ready"
            monitor.write_text(
                "import time\nfrom pathlib import Path\n"
                f"Path({str(monitor_ready)!r}).touch()\n"
                "while True: time.sleep(60)\n"
            )
            backend = folder / "backend.py"
            backend_ready = folder / "backend-ready"
            backend.write_text(
                "import os, time\nfrom pathlib import Path\n"
                f"Path({str(backend_ready)!r}).write_text(str(os.getpid()))\n"
                + ("time.sleep(0.3)\n" if keyboard_exit else "while True: time.sleep(60)\n")
            )
            if keyboard or keyboard_exit:
                selection = folder / "runtime-launch"
                selection.mkdir()
                (selection / "mode").write_text("keyboard\n")
            helper = folder / "helper"
            source = (ROOT / "src/vhp-root").read_text()
            source = source.replace("[[ $EUID == 0 && $# == 1 ]]", "[[ $# == 1 ]]")
            source = source.replace("/run/vhp", str(runtime))
            source = source.replace("/sys/class/backlight/amdgpu_bl0/brightness", str(brightness))
            source = source.replace("/home/.vhp/bin/vhusbdx86_64", str(server))
            source = source.replace("/home/.vhp/data/brightness-percent", str(preference))
            source = source.replace("/home/.vhp/bin/touch-stop.py", str(monitor))
            source = source.replace("/home/.vhp/bin/vhp_backend.py", str(backend))
            source = source.replace('exec /usr/bin/systemctl "$1" vhp.service', "exit 0")
            start_block = source.split("  start | start-gui | start-keyboard)", 1)[1].split(
                "  stop)", 1
            )[0]
            source = source.replace(start_block, "\n    exit 0\n    ;;\n")
            helper.write_text(source)
            helper.chmod(0o755)
            # sudo and systemctl mocks let the unmodified launcher loop run unprivileged.
            sudo = folder / "sudo"
            sudo.write_text('#!/bin/bash\nshift\nexec "$@"\n')
            sudo.chmod(0o755)
            systemctl = folder / "systemctl"
            systemctl.write_text("#!/bin/bash\nexit 0\n")
            systemctl.chmod(0o755)
            # This lifecycle fixture does not contact a real display server.
            (folder / "vhp_idle.py").write_text("# idle helper stub\n")
            launcher = folder / "launcher"
            launcher.write_text(
                (ROOT / "src/vhp.sh")
                .read_text()
                .replace("/home/.vhp/bin/vhp-root", str(helper))
                .replace("/usr/bin/systemctl", str(systemctl))
            )
            launcher.chmod(0o755)
            service = subprocess.Popen(
                [str(helper), "run"],
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            client = None
            try:
                # Bounded readiness check for this local test fixture.
                deadline = time.monotonic() + 3
                while brightness.read_text().strip() != "1":
                    if time.monotonic() > deadline or service.poll() is not None:
                        self.fail("Mock service failed to dim brightness")
                    time.sleep(0.02)
                self.assertFalse((runtime / "stopping").exists())
                if brightness_reset:
                    brightness.write_text("140\n")
                    deadline = time.monotonic() + 3
                    while brightness.read_text().strip() != "1":
                        if time.monotonic() > deadline or service.poll() is not None:
                            self.fail("External brightness change was not corrected")
                        time.sleep(0.02)
                if kill_launcher:
                    env = dict(os.environ, PATH=f"{folder}:" + os.environ["PATH"])
                    client = subprocess.Popen(
                        [str(launcher)],
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    time.sleep(1.2)
                    self.assertIsNone(client.poll())
                    client.kill()  # No EXIT trap can run.
                    client.wait(timeout=2)
                    if stale_lease:
                        # The terminal case covers the real ten-second expiry.
                        # Here test keyboard-child cleanup on an expired lease,
                        # without repeating that same wall-clock wait.
                        try:
                            os.killpg(client.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        (runtime / "lease").write_text("0\n")
                elif touch_exit:
                    # The real monitor starts only after stale requests are cleared.
                    # Brightness alone does not establish that startup has finished.
                    deadline = time.monotonic() + 3
                    ready = backend_ready if keyboard else monitor_ready
                    while not ready.exists():
                        if time.monotonic() > deadline or service.poll() is not None:
                            self.fail("Mock input child did not become ready")
                        time.sleep(0.02)
                    (runtime / "touch-stop").touch()
                    if keyboard:
                        # Cover at least one service-loop iteration with the marker present.
                        time.sleep(1.2)
                        self.assertIsNone(service.poll(), "Keyboard mode honored a corner request")
                        self.assertFalse((runtime / "stopping").exists())
                        service.send_signal(signal.SIGTERM)
                elif not keyboard_exit:
                    service.send_signal(signal.SIGTERM)
                if stop_during_cleanup:
                    # Backend disconnect begins EXIT cleanup; systemd then sends
                    # TERM to the entire group when the launcher requests stop.
                    deadline = time.monotonic() + 3
                    while not (runtime / "stopping").exists():
                        if time.monotonic() > deadline or service.poll() is not None:
                            self.fail("Mock service did not enter cleanup")
                        time.sleep(0.02)
                    os.killpg(service.pid, signal.SIGTERM)
                    service.wait(timeout=6)
                    if service.returncode != 0:
                        # A broken cleanup can leave the fake TERM-ignoring
                        # server holding stdout open; don't let that hide failure.
                        try:
                            os.killpg(service.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                output, _ = service.communicate(timeout=17)
                self.assertEqual(service.returncode, 0, output)
                self.assertTrue((runtime / "stopping").exists())
                status = subprocess.run([str(helper), "keepalive"], capture_output=True, timeout=2)
                self.assertEqual(status.returncode, 2)
                if kill_launcher:
                    self.assertIn("heartbeat expired", output)
                if touch_exit:
                    if keyboard:
                        self.assertNotIn("Touchscreen requested shutdown.", output)
                    else:
                        self.assertIn("Touchscreen requested shutdown.", output)
                if keyboard_exit:
                    self.assertIn("Keyboard backend exited", output)
                if keyboard or keyboard_exit:
                    self.assertFalse(monitor_ready.exists(), "Keyboard mode started corner monitor")
                    self.assertTrue(backend_ready.exists())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(backend_ready.read_text()), 0)
                if brightness_reset:
                    self.assertIn("Backlight changed externally (140)", output)
                self.assertIn("Saved backlight brightness=73", output)
                self.assertIn("Restored backlight brightness=73", output)
                self.assertEqual(brightness.read_text().strip(), "73")
                self.assertEqual(brightness.stat().st_mode & 0o777, 0o640)
            finally:
                for process in (client, service):
                    if process is not None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=3)
                if service.stdout:
                    service.stdout.close()


if __name__ == "__main__":
    unittest.main()
