#!/usr/bin/env python3
"""Unprivileged, pre-launch repair dialog. Never collect or forward a password."""

import os
import signal
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path("/home/.deckthere/bin")
HELPER = ROOT / "deckthere-root"
REPAIR = ROOT / "deckthere_repair.py"
DIALOG = "/usr/bin/kdialog"
AGENT = "/usr/lib/polkit-kde-authentication-agent-1"
FALLBACK = (
    "Switch to Desktop Mode and launch DeckThere again to repair, "
    "or run setup.sh there. No sharing was started."
)


class Cancelled(Exception):
    pass


def environment():
    # Retain the real Steam display/app context, but use native system libraries.
    env = os.environ.copy()
    for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    return env


def run(args, timeout=10):
    return subprocess.run(
        args,
        env=environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    ).returncode


@contextmanager
def authentication_agent():
    agent = None
    try:
        present = run(
            [
                "/usr/bin/pgrep",
                "-u",
                str(os.getuid()),
                "-f",
                "^/usr/lib/polkit-kde-authentication-agent-1( |$)",
            ]
        )
        if present == 1:
            # Gaming Mode normally has no agent. This is the stock normal-user
            # agent, only for this interaction, not an installed/boot service.
            agent = subprocess.Popen(
                [AGENT],
                env=environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif present != 0:
            raise OSError("Could not inspect the native authentication agent")
        # Start before the confirmation dialog, allowing native registration
        # while the user reads it. Never retry authorization on a startup failure.
        yield
    finally:
        if agent is not None and agent.poll() is None:
            agent.terminate()
            try:
                agent.wait(timeout=3)
            except subprocess.TimeoutExpired:
                agent.kill()
                agent.wait(timeout=3)


def trusted(path):
    for current in [*reversed(path.parents), path]:
        info = current.lstat()
        kind = stat.S_ISREG if current == path else stat.S_ISDIR
        if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return False
    return True


def graphical():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def message(text):
    print(text, file=sys.stderr)
    if graphical():
        try:
            run([DIALOG, "--title", "DeckThere", "--error", text], timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            pass


def check():
    # Ignore cached authentication for this invocation; never prompt here.
    return run(["/usr/bin/sudo", "-k", "-n", str(HELPER), "check"])


def launch():
    if os.geteuid() == 0:
        print("Launch DeckThere as your normal user, not root.", file=sys.stderr)
        return 1
    try:
        if not trusted(HELPER) or not trusted(REPAIR):
            message("DeckThere's trusted repair files are unavailable. " + FALLBACK)
            return 1
        status = check()
        if status == 0:
            return 0
        if status == 2 or run(["/usr/bin/python3", "-I", str(REPAIR), "--available"]):
            message("DeckThere's installation needs manual setup. " + FALLBACK)
            return 1
        if not graphical():
            message("DeckThere needs administrator authorization to repair. " + FALLBACK)
            return 1
        with authentication_agent():
            choice = run(
                [
                    DIALOG,
                    "--title",
                    "DeckThere",
                    "--yes-label",
                    "Repair",
                    "--no-label",
                    "Cancel",
                    "--yesno",
                    "DeckThere needs to repair its system integration.\n\n"
                    "The system will request administrator authorization. Only DeckThere's "
                    "service and helper-access rule will be restored. Settings, license and "
                    "Steam shortcuts will not change.",
                ],
                timeout=120,
            )
            if choice != 0:
                return 1
            # No hidden terminal password prompt if the native agent is unavailable.
            result = run(
                ["/usr/bin/pkexec", "--disable-internal-agent", str(REPAIR), "--repair"],
                timeout=180,
            )
        if result == 126:  # Authentication dialog cancelled/dismissed.
            return 1
        if result != 0 or check() != 0:
            message("DeckThere could not authorize or verify the repair. " + FALLBACK)
            return 1
        return 0
    except (OSError, subprocess.TimeoutExpired):
        message("DeckThere's launch check or repair could not finish. " + FALLBACK)
        return 1


def main():
    def cancel(signum, frame):
        # Complete owned-agent cleanup even if Steam sends repeated group TERM.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise Cancelled()

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return launch()
    except Cancelled:
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
