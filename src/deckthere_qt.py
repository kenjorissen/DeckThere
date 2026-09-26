#!/usr/bin/env python3
"""Private Qt bootstrap. Runs only as the desktop user, never from sudo/systemd."""

import sys
from pathlib import Path

base = Path(__file__).resolve().parent
sys.path[:0] = [str(base / "pylib"), str(base)]

if __name__ == "__main__":
    import PySide6
    import shiboken6

    for package in (PySide6, shiboken6):
        if not Path(package.__file__).resolve().is_relative_to((base / "pylib").resolve()):
            raise SystemExit("Private Qt runtime missing; refusing a system-package fallback")

    if sys.argv[1:] == ["--check-runtime"]:
        if PySide6.__version__ != shiboken6.__version__:
            raise SystemExit("Mismatched private Qt packages")
        from PySide6 import QtCore, QtGui, QtQml, QtQuick  # noqa: F401
    else:
        from deckthere_ui import main

        sys.exit(main())
