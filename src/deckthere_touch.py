"""Session-scoped native touch for the Gamescope X11 GUI.

Avoid Gamescope's overlapping-touch mouse emulation bug (upstream PR #2346).
The supervisor owns this guard so UI exit/crash still restores the prior mode.
Only existing compositor properties are used; no Steam configuration is edited.
"""

import os
import re
import syslog

import deckthere_idle as idle

MODE = "STEAM_TOUCH_CLICK_MODE"
APP = "GAMESCOPE_FOCUSED_APP"
WINDOW = "GAMESCOPE_FOCUSED_WINDOW"
PASSTHROUGH = 4


def cardinal(output, name, empty=False):
    match = re.search(rf"^{re.escape(name)}\(CARDINAL\) = ([0-9]+)$", output, re.M)
    if match and int(match[1]) <= 0xFFFFFFFF:
        return int(match[1])
    if empty and re.search(rf"^{re.escape(name)}\(CARDINAL\) =[ \t]*$", output, re.M):
        return 0
    raise idle.IdleError(f"Missing or incompatible {name}")


class NativeTouch:
    def __init__(self, target=None, display=None, app_id=None, ui_pid=None):
        self.target = target
        self.display = display
        self.app_id = app_id
        self.ui_pid = ui_pid
        self.previous = None

    @staticmethod
    def log(message):
        syslog.openlog("deckthere-touch", syslog.LOG_PID, syslog.LOG_USER)
        syslog.syslog(syslog.LOG_INFO, f"DeckThere native-touch: {message}")

    @classmethod
    def start(cls, ui_pid):
        display = os.environ.get("DISPLAY", "")
        app_id = os.environ.get("SteamAppId", "")
        if (
            not idle.gaming_mode()
            or os.geteuid() == 0
            or os.environ.get("DECKTHERE_DISABLE_NATIVE_TOUCH") == "1"
            or not idle.DISPLAY_PATTERN.fullmatch(display)
            or not re.fullmatch(r"[0-9]{1,10}", app_id)
            or not 0 < int(app_id) <= 0xFFFFFFFF
            or int(app_id) == 769
            or (os.environ.get("WAYLAND_DISPLAY") and os.environ.get("QT_QPA_PLATFORM") != "xcb")
        ):
            return cls()
        try:
            pid, ticks = idle.compositor()
            targets = []
            for candidate in idle.displays():
                try:
                    idle.read_counter(candidate)
                    targets.append(idle.Target(candidate, pid, ticks))
                except idle.IdleError:
                    continue
            if len(targets) != 1:
                raise idle.IdleError("Cannot identify one Gamescope root")
            guard = cls(targets[0], display, int(app_id), ui_pid)
            guard.state()  # Refuse absent/unknown touch modes before any writes.
            return guard
        except idle.IdleError as exc:
            cls.log(f"unavailable ({exc})")
            return cls()

    def state(self):
        if idle.process_identity(self.target.pid) != self.target.ticks:
            raise idle.IdleError("Gamescope session changed")
        output = idle.command(
            ["/usr/bin/xprop", "-display", self.target.display, "-root", MODE, APP, WINDOW],
            self.target.display,
        )
        mode = cardinal(output, MODE)
        if mode > 6:
            raise idle.IdleError("Unknown Gamescope touch mode")
        return mode, cardinal(output, APP, empty=True), cardinal(output, WINDOW, empty=True)

    def owns_focus(self, app_id, window):
        if app_id != self.app_id or not window:
            return False
        output = idle.command(
            ["/usr/bin/xprop", "-display", self.display, "-id", hex(window), "_NET_WM_PID"],
            self.display,
        )
        return cardinal(output, "_NET_WM_PID") == self.ui_pid

    def write(self, mode):
        idle.command(
            [
                "/usr/bin/xprop",
                "-display",
                self.target.display,
                "-root",
                "-f",
                MODE,
                "32c",
                "-set",
                MODE,
                str(mode),
            ],
            self.target.display,
        )

    def release(self, current):
        if self.previous is not None:
            if current == PASSTHROUGH:
                self.write(self.previous)
                self.log(f"restored mode={self.previous}")
            # Steam or another owner may have already changed the mode. Keep it.
            self.previous = None

    def tick(self):
        if self.target is None:
            return
        try:
            mode, app_id, window = self.state()
            if self.owns_focus(app_id, window):
                if mode != PASSTHROUGH:
                    # Capture Steam's latest choice, including changes on rebind.
                    self.previous = mode
                    self.write(PASSTHROUGH)
                    self.log(f"enabled; previous mode={mode}")
            else:
                self.release(mode)
        except idle.IdleError as exc:
            self.log(f"disabled ({exc})")
            self.close()
            self.target = None

    def close(self):
        if self.target is None or self.previous is None:
            return
        try:
            mode, _, _ = self.state()
            self.release(mode)
        except idle.IdleError as exc:
            self.log(f"could not restore ({exc})")
