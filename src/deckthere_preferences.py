"""Normal-user startup preference, shared by setup, launcher and settings.

The legacy value 'keyboard' means GUI with the virtual USB keyboard enabled.
"""

import os
import pwd
import stat
import sys
import tempfile
from pathlib import Path

MODES = ("gui", "keyboard", "terminal")
DEFAULT = "gui"


def read_mode(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return DEFAULT
            raw = os.read(fd, 33)
            if len(raw) > 32:
                return DEFAULT
            data = raw.decode("ascii").strip()
        finally:
            os.close(fd)
        return data if data in MODES else DEFAULT
    except (OSError, UnicodeError):
        return DEFAULT


def save_mode(path, mode):
    if mode not in MODES:
        raise ValueError("Unknown startup mode")
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".launch-mode-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(mode + "\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_auto_dim(path):
    """A bounded, data-only preference. Fresh/invalid settings retain dimming."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return True
            return os.read(fd, 4) not in (b"0", b"0\n", b"0\r\n")
        finally:
            os.close(fd)
    except OSError:
        return True


def save_auto_dim(path, enabled):
    if type(enabled) is not bool:
        raise ValueError("Expected a boolean")
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".auto-dim-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("1\n" if enabled else "0\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def installed_auto_dim():
    """Service subprocess: drop privileges before reading any user-owned path.

    Only this root-installed module runs, never a module in the owner's home.
    Environment and caller-supplied paths cannot select the installation owner.
    """
    uid = int(Path("/home/.deckthere/bin/owner-uid").read_text())
    if not 0 < uid < 2**31:
        raise ValueError("Invalid installation owner")
    owner = pwd.getpwuid(uid)
    os.setgroups([])
    os.setgid(owner.pw_gid)
    os.setuid(uid)
    return read_auto_dim(Path(owner.pw_dir) / ".local/share/deckthere/auto-dim")


if __name__ == "__main__":
    if sys.argv[1:] == ["--installed-auto-dim"]:
        print(int(installed_auto_dim()))
    elif len(sys.argv) in (3, 4) and sys.argv[1] == "--auto-dim":
        if len(sys.argv) == 4:
            if sys.argv[3] not in ("0", "1"):
                raise SystemExit("Expected 0 or 1")
            save_auto_dim(sys.argv[2], sys.argv[3] == "1")
        else:
            print(int(read_auto_dim(sys.argv[2])))
    elif len(sys.argv) == 2:
        print(read_mode(sys.argv[1]))
    elif len(sys.argv) == 3:
        save_mode(sys.argv[1], sys.argv[2])
    else:
        raise SystemExit("Usage: deckthere_preferences.py FILE [gui|keyboard|terminal]")
