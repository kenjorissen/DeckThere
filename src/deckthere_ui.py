#!/usr/bin/env python3
"""Touch UI for DeckThere: big on-screen keyboard plus a status strip.

Runs as the normal user. All privileged work happens in the root backend over a
Unix socket; this process can only send an operation name, an HID key code, and
a boolean.
"""

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Property, QEvent, QObject, QSocketNotifier, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deckthere_dashboard  # noqa: E402
import deckthere_ipc  # noqa: E402
import deckthere_keyboard  # noqa: E402
import deckthere_preferences  # noqa: E402
import deckthere_sleep  # noqa: E402

RETRY_MS = 2000


def prefer_wayland(environment=None):
    """Choose the Wayland plugin when nothing else was requested.

    Qt's default on Linux is xcb, which needs XWayland's DISPLAY and a system
    libxcb-cursor0 that stock SteamOS may not have; both Desktop Mode and Gaming
    Mode are Wayland sessions, so the bundled Wayland plugin is the better
    default. An explicit QT_QPA_PLATFORM always wins.
    """
    environment = os.environ if environment is None else environment
    if environment.get("QT_QPA_PLATFORM") or not environment.get("WAYLAND_DISPLAY"):
        return False
    environment["QT_QPA_PLATFORM"] = "wayland"
    return True


class Bridge(QObject):
    """Socket client plus the touch-key state machine, exposed to QML."""

    changed = Signal()
    ended = Signal()
    sampled = Signal(object)
    keyboardFailed = Signal()
    brightnessFailed = Signal()

    def __init__(self, socket_path, parent=None, session=False):
        super().__init__(parent)
        self.socket_path = Path(socket_path)
        self.socket = None
        self.notifier = None
        self.reader = deckthere_ipc.Reader()
        self.keys = deckthere_keyboard.TouchKeys()
        self._layout = "us"
        self.session = session
        self.ever_connected = False
        self._connected = False
        self._shared = False
        self._keyboard = False
        self._stopping = False
        self._percent = 0
        self._auto_dim = False
        self._finished = False
        self._dashboard = {
            "clock": time.strftime("%H:%M"),
            "battery": "--%",
            "batteryState": "Unavailable",
            "local": "Unavailable",
            "clients": "Unavailable",
            "adaptiveWarning": True,
        }
        self.sampling = False
        self.next_battery = 0
        self.sampled.connect(self.receive_dashboard)
        self.retry = QTimer(self)
        self.retry.setInterval(RETRY_MS)
        self.retry.timeout.connect(self.connect)
        self.retry.start()
        self.connect()

    @Property("QVariantMap", notify=changed)
    def dashboard(self):
        return self._dashboard

    def start_dashboard(self):
        self.dashboard_timer = QTimer(self)
        self.dashboard_timer.setInterval(5000)
        self.dashboard_timer.timeout.connect(self.sample_dashboard)
        self.dashboard_timer.start()
        self.sample_dashboard()

    def sample_dashboard(self):
        if self.sampling:
            return
        self.sampling = True
        read_battery = time.monotonic() >= self.next_battery
        if read_battery:
            self.next_battery = time.monotonic() + 30

        def sample():
            result = {"clock": time.strftime("%H:%M")}
            try:
                result["local"], result["clients"] = deckthere_dashboard.network()
                result["adaptiveWarning"] = deckthere_dashboard.adaptive_warning()
                if read_battery:
                    result["battery"], result["batteryState"] = deckthere_dashboard.battery()
            finally:
                self.sampled.emit(result)

        threading.Thread(target=sample, daemon=True).start()

    @Slot(object)
    def receive_dashboard(self, result):
        self.sampling = False
        self._dashboard.update(result)
        self.changed.emit()

    # -- connection --------------------------------------------------------
    @Slot()
    def connect(self):
        if self._connected or self._finished:
            return
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.setblocking(False)
            connection.connect(str(self.socket_path))
        except OSError:
            connection.close()
            return
        self.socket = connection
        self._connected = True
        self.retry.stop()
        self.ever_connected = True
        self.reader = deckthere_ipc.Reader()
        self.notifier = QSocketNotifier(connection.fileno(), QSocketNotifier.Type.Read, self)
        self.notifier.activated.connect(self.read)
        self.send({"op": "status"})
        self.changed.emit()

    def drop(self):
        if self.notifier is not None:
            self.notifier.setEnabled(False)
            self.notifier.deleteLater()
            self.notifier = None
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None
        self._connected = False
        self._shared = False
        self._keyboard = False
        # Never leave a modifier stuck on the PC after a dropped connection.
        self.keys = deckthere_keyboard.TouchKeys()
        self.changed.emit()
        if self.session and self.ever_connected:
            self.ended.emit()
        elif not self._finished:
            self.retry.start()

    @Slot()
    def read(self):
        try:
            data = self.socket.recv(4096)
        except OSError:
            self.drop()
            return
        if not data:
            self.drop()
            return
        try:
            lines = self.reader.feed(data)
            for line in lines:
                self.handle(deckthere_ipc.decode_response(line))
        except deckthere_ipc.ProtocolError:
            self.drop()

    def handle(self, message):
        op = message["op"]
        if op == "status":
            self._shared = message["shared"]
            if self._keyboard and not message["keyboard"]:
                caps = self.keys.caps
                self.keys = deckthere_keyboard.TouchKeys()
                self.keys.caps = caps
            self._keyboard = message["keyboard"]
            self._stopping = message["stopping"]
            self._percent = message["percent"]
            self._auto_dim = message["auto_dim"]
            if message["layout"] != self.layout:
                self._layout = message["layout"]
            self.changed.emit()
        elif op == "keyboard_error":
            self.keyboardFailed.emit()
        elif op == "brightness_error":
            self.brightnessFailed.emit()
        elif op == "pong":
            pass

    def send(self, message):
        if self.socket is None:
            return
        try:
            self.socket.sendall(deckthere_ipc.encode(message))
        except OSError:
            self.drop()

    def send_keys(self, events):
        for code, down in events:
            self.send({"op": "key", "code": code, "down": down})

    # -- operations --------------------------------------------------------
    @Slot(int)
    def press(self, code):
        if not self._keyboard:
            return
        if self.layout == "ko-104" and code in (230, 228):
            # Korean 101/104 Type 1 uses these as IME commands, not modifiers.
            self.send_keys([(code, True), (code, False)])
            return
        self.send_keys(self.keys.press(code))
        self.changed.emit()

    @Slot(int)
    def release(self, code):
        if self.layout == "ko-104" and code in (230, 228):
            return
        self.send_keys(self.keys.release(code))
        self.changed.emit()

    @Slot(str)
    def setLayout(self, layout):
        if layout in deckthere_ipc.LAYOUTS:
            self.clear()
            self._layout = layout
            self.send({"op": "layout", "layout": layout})
            self.changed.emit()

    @Slot()
    def clear(self):
        caps = self.keys.caps
        self.keys = deckthere_keyboard.TouchKeys()
        self.keys.caps = caps  # Releasing keys does not toggle the PC's Caps Lock.
        self.send({"op": "clear"})
        self.changed.emit()

    @Slot()
    def stop(self):
        self.send({"op": "stop"})
        self._finished = True
        self.retry.stop()
        self.changed.emit()

    # -- properties --------------------------------------------------------
    @Property(bool, notify=changed)
    def connected(self):
        return self._connected

    @Property(bool, notify=changed)
    def keyboardEnabled(self):
        return self._keyboard

    @Property(bool, notify=changed)
    def shared(self):
        return self._shared

    @Property(bool, notify=changed)
    def stopping(self):
        return self._stopping

    @Property(bool, notify=changed)
    def autoDim(self):
        return self._auto_dim

    @Property(int, notify=changed)
    def percent(self):
        return self._percent

    @Property(bool, notify=changed)
    def shiftActive(self):
        return self.keys.shift_active

    @Property(bool, notify=changed)
    def altgrActive(self):
        return self.keys.altgr_active

    @Property(bool, notify=changed)
    def capsActive(self):
        return self.keys.caps

    @Property("QVariantList", constant=True)
    def layoutNames(self):
        return [
            {"id": name, "label": entry["name"], "kind": entry["kind"], "note": entry["note"]}
            for name, entry in deckthere_keyboard.CATALOG.items()
        ]

    @Property(str, notify=changed)
    def layout(self):
        return self._layout

    @Property(str, notify=changed)
    def layoutNote(self):
        return deckthere_keyboard.CATALOG[self.layout]["note"]

    @Property(str, notify=changed)
    def layoutName(self):
        return deckthere_keyboard.LAYOUT_NAMES[self.layout]

    @Property("QVariantList", notify=changed)
    def rows(self):
        return [
            [self.keys.decorated(key) for key in row]
            for row in deckthere_keyboard.layout_grid(self.layout)
        ]

    @Property(int, constant=True)
    def columns(self):
        return 1000


class Settings(QObject):
    """User preferences and authenticated live keyboard/brightness requests."""

    changed = Signal()

    def __init__(self, bridge=None, path=None):
        super().__init__()
        self.bridge = bridge
        self.path = Path(path) if path is not None else Path(__file__).with_name("launch-mode")
        self._mode = deckthere_preferences.read_mode(self.path)
        self._auto_dim_launch = deckthere_preferences.read_auto_dim(self.path.with_name("auto-dim"))
        self._haptics_enabled, self._haptics_strength = deckthere_preferences.read_haptics(
            self.path.with_name("haptics")
        )
        self.pending_dim = None
        self._message = "Startup changes apply next launch."
        self.pending = None
        self._sleep_state = {}
        self._sleep_minutes = deckthere_sleep.read_minutes(self.path.parent)
        self._last_activity = 0.0
        self._activity_failed = False
        self.sleep_timer = QTimer(self)
        self.sleep_timer.setInterval(250)
        self.sleep_timer.timeout.connect(self.refreshSleep)
        self.sleep_timer.start()
        if bridge is not None:
            bridge.changed.connect(self.refresh)
            bridge.keyboardFailed.connect(self.failed)
            bridge.brightnessFailed.connect(self.dimFailed)

    @Property(bool, notify=changed)
    def hapticsEnabled(self):
        return self._haptics_enabled

    @Property(str, notify=changed)
    def hapticsStrength(self):
        return self._haptics_strength

    def save_haptics(self, enabled, strength):
        try:
            deckthere_preferences.save_haptics(self.path.with_name("haptics"), enabled, strength)
            self._haptics_enabled, self._haptics_strength = enabled, strength
            self._message = (
                "Haptics saved; applies to the next connection event and future sessions."
            )
        except (OSError, ValueError):
            self._message = "Could not save haptic preference."
        self.changed.emit()

    @Slot()
    def toggleHaptics(self):
        self.save_haptics(not self._haptics_enabled, self._haptics_strength)

    @Slot(str)
    def saveHapticsStrength(self, strength):
        self.save_haptics(self._haptics_enabled, strength)

    @Property(bool, notify=changed)
    def autoDimLaunch(self):
        return self._auto_dim_launch

    @Property(bool, notify=changed)
    def autoDimNow(self):
        return self.bridge is not None and self.bridge.autoDim

    @Property(bool, notify=changed)
    def hasSession(self):
        return self.bridge is not None and self.bridge.connected

    @Property(bool, notify=changed)
    def canDim(self):
        return (
            self.bridge is not None
            and self.bridge.connected
            and not self.bridge.stopping
            and self.pending_dim is None
        )

    @Slot()
    def toggleAutoDimLaunch(self):
        enabled = not self._auto_dim_launch
        try:
            deckthere_preferences.save_auto_dim(self.path.with_name("auto-dim"), enabled)
            self._auto_dim_launch = enabled
            self._message = "Auto-dim default saved; applies next launch."
        except (OSError, ValueError):
            self._message = "Could not save auto-dim preference."
        self.changed.emit()

    @Slot()
    def toggleAutoDimNow(self):
        if not self.canDim:
            return
        self.pending_dim = not self.autoDimNow
        self._message = "Updating auto-dim for this session…"
        self.changed.emit()
        self.bridge.send({"op": "auto_dim", "enabled": self.pending_dim})

    @Slot()
    def dimFailed(self):
        self.pending_dim = None
        self._message = "Could not change auto-dim. Sharing continues; check diagnostics."
        self.changed.emit()

    @Property(int, notify=changed)
    def sleepMinutes(self):
        return self._sleep_minutes

    @Property(str, notify=changed)
    def sleepMessage(self):
        return self._sleep_state.get("message", "")

    @Property(bool, notify=changed)
    def sleepWarning(self):
        return self._sleep_state.get("status") == "warning"

    @Slot(int)
    def saveSleep(self, minutes):
        try:
            deckthere_sleep.save_minutes(minutes, self.path.parent)
            self._sleep_minutes = minutes
            self._message = "Sleep preference saved; applies now and next launch."
        except (OSError, ValueError):
            self._message = "Could not save sleep preference."
        self.changed.emit()

    @Slot()
    def refreshSleep(self):
        state = deckthere_sleep.visible_state(self.path.parent)
        minutes = deckthere_sleep.read_minutes(self.path.parent)
        if state != self._sleep_state or minutes != self._sleep_minutes:
            self._sleep_state, self._sleep_minutes = state, minutes
            self.changed.emit()

    @Slot()
    def cancelSleep(self):
        try:
            deckthere_sleep.activity(self.path.parent)
            self._activity_failed = False
            self._sleep_state = {}
            self.changed.emit()
        except OSError:
            self._activity_failed = True

    @Slot()
    def confirmSleepWarning(self):
        if not self._activity_failed:
            try:
                deckthere_sleep.warning_shown(self.path.parent)
            except OSError:
                pass  # Missing warning acknowledgement prevents sleep.

    def eventFilter(self, watched, event):
        if (
            self._sleep_minutes
            and event.type()
            in (
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseMove,
                QEvent.Type.Wheel,
                QEvent.Type.TouchBegin,
                QEvent.Type.TouchUpdate,
                QEvent.Type.TouchEnd,
            )
            and time.monotonic() - self._last_activity >= 0.1
        ):
            self._last_activity = time.monotonic()
            self.cancelSleep()
        return False

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def busy(self):
        return self.pending is not None

    @Property(bool, notify=changed)
    def keyboardEnabled(self):
        return self.bridge is not None and self.bridge.keyboardEnabled

    @Property(bool, notify=changed)
    def canToggle(self):
        return (
            self.bridge is not None
            and self.bridge.connected
            and not self.bridge.stopping
            and not self.busy
        )

    @Slot(str)
    def save(self, mode):
        try:
            deckthere_preferences.save_mode(self.path, mode)
        except (OSError, ValueError):
            self._message = "Could not save startup preference."
        else:
            self._mode = mode
            self._message = "Saved. Startup changes apply next launch."
        self.changed.emit()

    @Slot()
    def toggleKeyboard(self):
        if not self.canToggle:
            return
        self.pending = not self.bridge.keyboardEnabled
        self._message = (
            "Starting virtual USB keyboard…" if self.pending else "Stopping virtual USB keyboard…"
        )
        self.changed.emit()
        self.bridge.send({"op": "keyboard_start" if self.pending else "keyboard_stop"})

    @Slot()
    def failed(self):
        action = "stop" if self.pending is False else "start"
        self.pending = None
        self._message = (
            f"Keyboard could not {action}. Controller sharing continues; check diagnostics."
        )
        self.changed.emit()

    @Slot()
    def refresh(self):
        if self.pending_dim is not None:
            if not self.bridge.connected:
                self.dimFailed()
            elif self.bridge.autoDim == self.pending_dim:
                self.pending_dim = None
                self._message = "Auto-dim changed for this session only."
        if self.pending is not None:
            if not self.bridge.connected:
                self.failed()
                return
            if self.bridge.keyboardEnabled == self.pending:
                action = "started" if self.pending else "stopped"
                self.pending = None
                self._message = f"Keyboard {action} for this session only."
        self.changed.emit()


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description="DeckThere touch UI")
    parser.add_argument(
        "--session", action="store_true", help="exit when the supervised backend ends"
    )
    parser.add_argument(
        "--settings", action="store_true", help="startup settings only; no service or keyboard"
    )
    parser.add_argument("--socket", type=Path, default=Path("/run/deckthere/gui.sock"))
    parser.add_argument("--qml", type=Path, default=Path(__file__).with_suffix(".qml"))
    parser.add_argument(
        "--self-test",
        type=float,
        metavar="SECONDS",
        help="load the UI, then exit successfully (offscreen checks)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    options = parse_arguments(sys.argv[1:] if argv is None else argv)
    prefer_wayland()  # Must happen before QGuiApplication selects a backend.
    application = QGuiApplication(sys.argv[:1])
    application.setApplicationName("DeckThere")
    # Exposed as a root-object property rather than a context property: Qt clears
    # context properties before destroying the object tree, so every binding would
    # re-evaluate against a null during shutdown. Initial properties avoid that.
    bridge = None if options.settings else Bridge(options.socket, session=options.session)
    preferences = Settings(bridge)
    application.installEventFilter(preferences)
    if bridge is not None:
        bridge.ended.connect(application.quit)
        bridge.start_dashboard()
        if options.session:
            QTimer.singleShot(15000, lambda: None if bridge.connected else application.quit())
        application.aboutToQuit.connect(bridge.clear)
    # Python signal handlers need the Qt loop to periodically return to Python.
    import signal

    signal.signal(signal.SIGTERM, lambda *_: application.quit())
    signal.signal(signal.SIGINT, lambda *_: application.quit())
    signal_timer = QTimer(application)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(1000)
    engine = QQmlApplicationEngine()
    properties = {"preferences": preferences}
    if bridge is not None:
        properties["deckthere"] = bridge
    engine.setInitialProperties(properties)
    qml = Path(__file__).with_name("deckthere_settings.qml") if options.settings else options.qml
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        print("UI failed to load.", file=sys.stderr)
        return 1
    if options.self_test is not None:
        QTimer.singleShot(int(options.self_test * 1000), application.quit)
    if bridge is not None and os.environ.get("DECKTHERE_UI_SMOKE"):
        # Report a summary then quit; used by the automated offscreen check.
        QTimer.singleShot(
            0,
            lambda: print(
                f"loaded rows={len(bridge.rows)} columns={bridge.columns} "
                f"layout={bridge.layout} connected={bridge.connected}",
                flush=True,
            ),
        )
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
