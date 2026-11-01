"""Nonblocking Settings controls for explicit stable-release updates."""

import json
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, Signal, Slot

import deckthere_update


class Updates(QObject):
    changed = Signal()
    ready = Signal()

    def __init__(self, base, session=False, parent=None):
        super().__init__(parent)
        self.base = Path(base)
        self.session = session
        self.release = None
        self._busy = False
        self._confirming = False
        self.action = None
        self.cancelled = False
        self._message = "Check for published stable releases."
        try:
            info = deckthere_update.installed(self.base)
            self._message = f"Installed {info['version']}. Check for stable releases."
            if info["channel"] != "stable":
                self._message = "Development branch: update manually. Stable releases only here."
        except deckthere_update.UpdateError:
            self._message = "Run setup once to enable in-app updates."
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.failed)

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(bool, notify=changed)
    def canApply(self):
        return self.session and self.release is not None and not self._busy

    @Property(bool, notify=changed)
    def confirming(self):
        return self._confirming

    @Slot()
    def check(self):
        if not self._busy:
            self.release = None
            self._confirming = False
            self.start("check")

    def start(self, action):
        self.cancelled = False
        self.action = action
        self._busy = True
        self._message = (
            "Checking stable releases…"
            if action == "check"
            else "Downloading and validating update… Sharing continues until it is ready."
        )
        args = ["-I", str(self.base / "deckthere_update.py"), action, "--base", str(self.base)]
        if action == "prepare":
            args += ["--tag", self.release["version"], "--commit", self.release["commit"]]
        self.process.start(sys.executable, args)
        self.changed.emit()

    @Slot()
    def requestApply(self):
        if self.canApply:
            self._confirming = True
            self._message = f"Install {self.release['version']}? Sharing will stop after download. Administrator authorization is required. Relaunch afterward."
            self.changed.emit()

    @Slot()
    def apply(self):
        if self.canApply and self._confirming:
            self._confirming = False
            self.start("prepare")

    @Slot()
    def cancel(self):
        self.cancelled = True
        self._confirming = False
        if self.process.state() != QProcess.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(3000):
                self.process.kill()
                self.process.waitForFinished(1000)
        self._busy = False
        self._message = "Update cancelled."
        self.changed.emit()

    def failed(self, *_):
        self._busy = False
        self._message = "Could not run the updater. Check your connection or rerun setup."
        self.changed.emit()

    def finished(self, code, status):
        self._busy = False
        if self.cancelled:
            return
        try:
            result = json.loads(bytes(self.process.readAllStandardOutput()))
            if code or status != QProcess.NormalExit or "error" in result:
                self._message = result.get("error", "Update failed. Try again.")
            elif self.action == "check":
                self.release = result["release"] if result["available"] else None
                self._message = result["message"]
                if self.release is not None and not self.session:
                    self._message += " Launch the GUI to apply it."
            elif result.get("ready"):
                self._message = result["message"]
                self.changed.emit()
                self.ready.emit()
                return
        except (ValueError, KeyError, TypeError):
            self._message = "Update did not finish. Check your connection and try again."
        self.changed.emit()
