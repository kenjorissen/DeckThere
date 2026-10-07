"""Startup preferences shared by setup, launchers and settings.

The mode 'keyboard' means GUI with the virtual USB keyboard enabled.
"""

import os
import pwd
import stat
import sys
import tempfile
from pathlib import Path

MODES = ("gui", "keyboard", "terminal")
DEFAULT = "gui"
HAPTIC_STRENGTHS = ("quiet", "normal", "strong")
HAPTIC_DEFAULT = (True, "normal")


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


def read_haptics(path):
    """Small data-only preference; never follow a final symlink or open a FIFO blocking."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return HAPTIC_DEFAULT
            raw = os.read(fd, 65)
        finally:
            os.close(fd)
        if len(raw) > 64:
            return HAPTIC_DEFAULT
        enabled, strength = raw.decode("ascii").split()
        if enabled in ("0", "1") and strength in HAPTIC_STRENGTHS:
            return enabled == "1", strength
    except (OSError, UnicodeError, ValueError):
        pass
    return HAPTIC_DEFAULT


def save_haptics(path, enabled, strength):
    if type(enabled) is not bool or strength not in HAPTIC_STRENGTHS:
        raise ValueError("Invalid haptic preference")
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".haptics-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(f"{int(enabled)} {strength}\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def installed_preference_path(name):
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
    return Path(owner.pw_dir) / ".local/share/deckthere" / name


def installed_auto_dim():
    return read_auto_dim(installed_preference_path("auto-dim"))


if __name__ == "__main__":
    if sys.argv[1:] == ["--installed-auto-dim"]:
        print(int(installed_auto_dim()))
    elif sys.argv[1:] == ["--installed-haptics"]:
        enabled, strength = read_haptics(installed_preference_path("haptics"))
        print(strength if enabled else "off")
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
