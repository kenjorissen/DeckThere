"""Normal-user startup preference, shared by setup, launcher and settings.

The legacy value 'keyboard' means GUI with the virtual USB keyboard enabled.
"""

import os
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


if __name__ == "__main__":
    if len(sys.argv) == 2:
        print(read_mode(sys.argv[1]))
    elif len(sys.argv) == 3:
        save_mode(sys.argv[1], sys.argv[2])
    else:
        raise SystemExit("Usage: deckthere_preferences.py FILE [gui|keyboard|terminal]")
