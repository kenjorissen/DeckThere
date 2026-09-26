#!/usr/bin/env python3
"""Touch UI for VHP: big on-screen keyboard plus a status strip.

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

from PySide6.QtCore import Property, QObject, QSocketNotifier, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

sys.path.insert(0, str(Path(__file__).resolve().parent))

import vhp_dashboard  # noqa: E402
import vhp_ipc  # noqa: E402
import vhp_keyboard  # noqa: E402
import vhp_preferences  # noqa: E402

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

    def __init__(self, socket_path, parent=None, session=False):
        super().__init__(parent)
        self.socket_path = Path(socket_path)
        self.socket = None
        self.notifier = None
        self.reader = vhp_ipc.Reader()
        self.keys = vhp_keyboard.TouchKeys()
        self._layout = "us"
        self.session = session
        self.ever_connected = False
        self._connected = False
        self._shared = False
        self._keyboard = False
        self._stopping = False
        self._percent = 0
        self._finished = False
        self._dashboard = {
            "clock": time.strftime("%H:%M"),
            "battery": "--%",
            "batteryState": "Unavailable",
            "local": "Unavailable",
            "clients": "Unavailable",
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
                result["local"], result["clients"] = vhp_dashboard.network()
                if read_battery:
                    result["battery"], result["batteryState"] = vhp_dashboard.battery()
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
        self.reader = vhp_ipc.Reader()
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
        self.keys = vhp_keyboard.TouchKeys()
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
                self.handle(vhp_ipc.decode_response(line))
        except vhp_ipc.ProtocolError:
            self.drop()

    def handle(self, message):
        op = message["op"]
        if op == "status":
            self._shared = message["shared"]
            self._keyboard = message["keyboard"]
            self._stopping = message["stopping"]
            self._percent = message["percent"]
            if message["layout"] != self.layout:
                self._layout = message["layout"]
            self.changed.emit()
        elif op == "keyboard_error":
            self.keyboardFailed.emit()
        elif op == "pong":
            pass

    def send(self, message):
        if self.socket is None:
            return
        try:
            self.socket.sendall(vhp_ipc.encode(message))
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
        if layout in vhp_ipc.LAYOUTS:
            self.clear()
            self._layout = layout
            self.send({"op": "layout", "layout": layout})
            self.changed.emit()

    @Slot()
    def clear(self):
        caps = self.keys.caps
        self.keys = vhp_keyboard.TouchKeys()
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
            for name, entry in vhp_keyboard.CATALOG.items()
        ]

    @Property(str, notify=changed)
    def layout(self):
        return self._layout

    @Property(str, notify=changed)
    def layoutNote(self):
        return vhp_keyboard.CATALOG[self.layout]["note"]

    @Property(str, notify=changed)
    def layoutName(self):
        return vhp_keyboard.LAYOUT_NAMES[self.layout]

    @Property("QVariantList", notify=changed)
    def rows(self):
        return [
            [self.keys.decorated(key) for key in row]
            for row in vhp_keyboard.layout_grid(self.layout)
        ]

    @Property(int, constant=True)
    def columns(self):
        return 1000


class Settings(QObject):
    """Unprivileged startup preferences; live keyboard requests stay on IPC."""

    changed = Signal()

    def __init__(self, bridge=None, path=None):
        super().__init__()
        self.bridge = bridge
        self.path = Path(path) if path is not None else Path(__file__).with_name("launch-mode")
        self._mode = vhp_preferences.read_mode(self.path)
        self._message = "Startup changes apply next launch."
        self.pending = None
        if bridge is not None:
            bridge.changed.connect(self.refresh)
            bridge.keyboardFailed.connect(self.failed)

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
    def canStart(self):
        return (
            self.bridge is not None
            and self.bridge.connected
            and not self.bridge.stopping
            and not self.busy
        )

    @Slot(str)
    def save(self, mode):
        try:
            vhp_preferences.save_mode(self.path, mode)
        except (OSError, ValueError):
            self._message = "Could not save startup preference."
        else:
            self._mode = mode
            self._message = "Saved. Startup changes apply next launch."
        self.changed.emit()

    @Slot(bool)
    def startKeyboard(self, remember):
        if not self.canStart:
            return
        if self.bridge.keyboardEnabled:
            if remember:
                self.save("keyboard")
            else:
                self._message = "Keyboard is already running."
                self.changed.emit()
            return
        self.pending = remember
        self._message = "Starting virtual USB keyboard…"
        self.changed.emit()
        self.bridge.send({"op": "keyboard_start"})

    @Slot()
    def failed(self):
        self.pending = None
        self._message = "Keyboard could not start. Controller sharing continues; check diagnostics."
        self.changed.emit()

    @Slot()
    def refresh(self):
        if self.pending is not None:
            if not self.bridge.connected:
                self.failed()
                return
            if self.bridge.keyboardEnabled:
                remember = self.pending
                self.pending = None
                if remember:
                    self.save("keyboard")
                else:
                    self._message = "Keyboard started for this session only."
        self.changed.emit()


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description="VHP touch UI")
    parser.add_argument(
        "--session", action="store_true", help="exit when the supervised backend ends"
    )
    parser.add_argument(
        "--settings", action="store_true", help="startup settings only; no service or keyboard"
    )
    parser.add_argument("--socket", type=Path, default=Path("/run/vhp/gui.sock"))
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
    application.setApplicationName("VirtualHerePad")
    # Exposed as a root-object property rather than a context property: Qt clears
    # context properties before destroying the object tree, so every binding would
    # re-evaluate against a null during shutdown. Initial properties avoid that.
    bridge = None if options.settings else Bridge(options.socket, session=options.session)
    preferences = Settings(bridge)
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
        properties["vhp"] = bridge
    engine.setInitialProperties(properties)
    qml = Path(__file__).with_name("vhp_settings.qml") if options.settings else options.qml
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        print("UI failed to load.", file=sys.stderr)
        return 1
    if options.self_test is not None:
        QTimer.singleShot(int(options.self_test * 1000), application.quit)
    if bridge is not None and os.environ.get("VHP_UI_SMOKE"):
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
