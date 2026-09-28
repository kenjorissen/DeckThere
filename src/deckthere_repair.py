#!/usr/bin/python3 -I
"""Fixed, root-owned integration repair. No downloads, settings or service starts."""

import fcntl
import os
import signal
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

BIN = Path("/home/.deckthere/bin")
UNIT = Path("/etc/systemd/system/deckthere.service")
RULE = Path("/etc/sudoers.d/zz-deckthere")
LOCK = Path("/run/deckthere-launch/lock")
FILES = (
    "deckthere-root",
    "deckthere_repair.py",
    "touch-stop.py",
    "vhusbdx86_64",
    "deckthere_backend.py",
    "deckthere_activity.py",
    "deckthere_hardware.py",
    "deckthere_keyboard.py",
    "deckthere_layouts.json",
    "deckthere_ipc.py",
    "owner-uid",
    "deckthere.service",
    "deckthere.sudoers",
)
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
LIMIT = 65536


class RepairError(Exception):
    pass


def safe_stat(path, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RepairError(f"Unsafe root-owned installation path: {path}")
    return info


def parents(path):
    for parent in reversed(path.parents):
        safe_stat(parent, directory=True)


def available():
    parents(BIN / "deckthere_repair.py")
    for name in FILES:
        info = safe_stat(BIN / name)
        if (
            name in ("deckthere-root", "deckthere_repair.py", "vhusbdx86_64")
            and not info.st_mode & 0o100
        ):
            raise RepairError(f"Installed executable is not executable: {name}")


def read_file(path):
    parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RepairError(f"Unsafe integration file: {path}")
        data = os.read(fd, LIMIT + 1)
        if len(data) > LIMIT:
            raise RepairError(f"Integration file too large: {path}")
        return data, stat.S_IMODE(info.st_mode)
    finally:
        os.close(fd)


def existing(path):
    try:
        return read_file(path)
    except FileNotFoundError:
        # A missing file is repairable, a missing/unsafe parent directory is not.
        parents(path)
        return None


def command(args):
    return subprocess.run(
        args,
        env=ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def service():
    result = command(
        [
            "/usr/bin/systemctl",
            "--system",
            "show",
            "deckthere.service",
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
            "-p",
            "FragmentPath",
            "-p",
            "DropInPaths",
            "-p",
            "NeedDaemonReload",
        ]
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (
        fields.get("LoadState") not in ("loaded", "not-found")
        or fields.get("ActiveState")
        not in ("inactive", "failed", "active", "activating", "deactivating", "reloading")
        or (result.returncode and fields.get("LoadState") != "not-found")
    ):
        raise RepairError("Cannot safely inspect the sharing service (possibly masked).")
    if fields.get("DropInPaths") or fields.get("FragmentPath") not in ("", str(UNIT)):
        raise RepairError("Custom service overrides require manual setup; not replacing them.")
    return fields


def payload():
    available()
    uid_data, _ = read_file(BIN / "owner-uid")
    if not uid_data.strip().isdigit() or not 0 < int(uid_data) < 2**31:
        raise RepairError("Invalid installed owner.")
    actor = os.environ.get("PKEXEC_UID")
    if actor is not None and actor != str(int(uid_data)):
        raise RepairError("Only the installed owner can request this repair.")
    return {
        UNIT: (read_file(BIN / "deckthere.service")[0], 0o644),
        RULE: (read_file(BIN / "deckthere.sudoers")[0], 0o440),
    }


def healthy(wanted, state):
    return (
        all(existing(path) == item for path, item in wanted.items())
        and state.get("LoadState") == "loaded"
        and state.get("FragmentPath") == str(UNIT)
        and state.get("NeedDaemonReload") == "no"
    )


def stage(path, data, mode):
    parents(path)
    fd, temporary = tempfile.mkstemp(prefix=".deckthere-repair-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


@contextmanager
def defer_termination():
    # Finish or roll back the bounded transaction before honoring launcher exit.
    # SIGKILL/power loss cannot be handled; each individual publication is atomic.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def repair(wanted):
    # Same lock as the fixed root start actions: never repair a live session.
    parents(LOCK.parent)
    LOCK.parent.mkdir(mode=0o700, exist_ok=True)
    safe_stat(LOCK.parent, directory=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        safe_stat(LOCK)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = service()
        if state["ActiveState"] not in ("inactive", "failed"):
            raise RepairError("Sharing is active; exit DeckThere before repairing.")
        if healthy(wanted, state):
            return
        previous = {path: existing(path) for path in wanted}
        staged = {}
        published = []
        try:
            for path, (data, mode) in wanted.items():
                if previous[path] != (data, mode):
                    staged[path] = stage(path, data, mode)
            rule = staged.get(RULE, RULE)
            if command(["/usr/sbin/visudo", "-cf", str(rule)]).returncode:
                raise RepairError("The saved sudo rule failed validation.")
            for path, temporary in staged.items():
                os.replace(temporary, path)
                published.append(path)
            if command(["/usr/sbin/visudo", "-c"]).returncode:
                raise RepairError("System sudo configuration needs manual attention.")
            if command(["/usr/bin/systemctl", "--system", "daemon-reload"]).returncode:
                raise RepairError("Could not reload system integration.")
            if not healthy(wanted, service()):
                raise RepairError("Integration repair could not be verified.")
        except BaseException:
            # Roll back only our two integration files, never other policy/config.
            for path in reversed(published):
                original = previous[path]
                if original is None:
                    path.unlink()
                else:
                    temporary = stage(path, *original)
                    try:
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
            if published:
                command(["/usr/bin/systemctl", "--system", "daemon-reload"])
            raise
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
    finally:
        os.close(fd)


def main(arguments):
    if arguments not in (["--available"], ["--check"], ["--repair"]):
        print("Usage: deckthere_repair.py --available | --check | --repair", file=sys.stderr)
        return 2
    try:
        if arguments == ["--available"]:
            available()  # Read-only metadata check, also usable without privilege.
            return 0
        if os.geteuid() != 0:
            raise RepairError("Administrator authorization is required.")
        wanted = payload()
        if arguments == ["--check"]:
            return 0 if healthy(wanted, service()) else 1
        with defer_termination():
            repair(wanted)
        return 0
    except (OSError, ValueError, RepairError, subprocess.TimeoutExpired) as exc:
        print(f"DeckThere repair unavailable: {exc}. Rerun setup in Desktop Mode.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
