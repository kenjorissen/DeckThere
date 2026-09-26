#!/usr/bin/env python3
"""Unprivileged supervisor for the installed Qt UI and its service lease."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HELPER = "/home/.vhp/bin/vhp-root"
BASE = Path(__file__).resolve().parent
# -I excludes the script directory; load only our installed normal-user modules.
sys.path.insert(0, str(BASE))
from vhp_idle import IdleError, IdleKeepalive  # noqa: E402


def helper(action, timeout=20):
    return subprocess.run(
        ["sudo", "-n", HELPER, action], stdin=subprocess.DEVNULL, timeout=timeout, check=False
    ).returncode


def main(keyboard=False):
    if os.geteuid() == 0:
        raise SystemExit("Run VirtualHerePad as your normal user, not with sudo")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        raise SystemExit("No graphical session. Launch from Steam or Desktop Mode.")
    command = ["/usr/bin/python3", "-I", str(BASE / "vhp_qt.py")]
    # Fail before starting privileged hardware if the private Qt install is broken.
    check = subprocess.run(command + ["--check-runtime"], check=False, timeout=20)
    if check.returncode:
        raise SystemExit("Qt runtime unavailable. Rerun setup.sh --gui, or use --terminal.")
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ui = None
    started = False
    try:
        idle = IdleKeepalive.start()
        if stopping:
            return 0
        if helper("start-keyboard" if keyboard else "start-gui"):
            return 1  # Do not stop a session owned by another launcher.
        started = True
        if stopping:
            return 0
        ui = subprocess.Popen(command + ["--session"], stdin=subprocess.DEVNULL)
        while not stopping and ui.poll() is None:
            status = helper("keepalive", timeout=4)
            if status:
                if status != 2:
                    print("VHP service/heartbeat ended; closing the UI.", file=sys.stderr)
                break
            idle.tick()
            time.sleep(1)
        return ui.returncode or 0
    except IdleError as exc:
        print(
            f"VHP idle protection failed: {exc}. See README: Gaming Mode idle handling.",
            file=sys.stderr,
        )
        return 1
    finally:
        if ui is not None and ui.poll() is None:
            ui.terminate()
            try:
                ui.wait(timeout=3)
            except subprocess.TimeoutExpired:
                ui.kill()
                ui.wait(timeout=3)
        if started:
            print("Stopping VirtualHerePad…", flush=True)
            try:
                if helper("stop"):
                    print("Stop failed; the service lease will expire.", file=sys.stderr)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"Stop failed; the service lease will expire: {exc}", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VirtualHerePad GUI supervisor")
    parser.add_argument("--keyboard", action="store_true")
    sys.exit(main(parser.parse_args().keyboard))
