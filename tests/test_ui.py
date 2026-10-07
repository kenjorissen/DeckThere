"""Integration tests for the Qt UI bridge against a real backend.

These exercise the full path a key press takes: touch -> bridge -> socket ->
backend -> HID report. They run unprivileged by injecting fake hardware into the
backend, and are skipped entirely when PySide6 is not installed.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "tests"))

try:
    from PySide6.QtCore import QCoreApplication, QPointF, Qt, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    # Importing QtQuick registers the QQuickItem* converter that
    # QQuickWindow.contentItem needs; without it PySide6 raises.
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from test_backend import FakeGadget, Harness
    from test_packaged_backend import FakeVolume

    import deckthere_backend
    import deckthere_keyboard
    import deckthere_ui

    HAVE_QT = True
except ImportError as error:  # pragma: no cover - depends on the environment
    HAVE_QT = False
    IMPORT_ERROR = error

KEY_A = bytes([0, 0, 4, 0, 0, 0, 0, 0])
SHIFT_A = bytes([0b00000010, 0, 4, 0, 0, 0, 0, 0])


def walk(item):
    """Every item in a QML visual tree, parents before children."""
    yield item
    for child in item.childItems():
        yield from walk(child)


def pump(seconds, until=None):
    """Run the Qt event loop for a while, optionally stopping early."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class QtTestCase(unittest.TestCase):
    """Shared Qt application and bridge construction."""

    @classmethod
    def setUpClass(cls):
        cls.application = QGuiApplication.instance() or QGuiApplication([])

    def bridge_for(self, harness):
        bridge = deckthere_ui.Bridge(harness.options.socket)
        # Close the socket even if an assertion fails part-way through.
        self.addCleanup(bridge.drop)
        return bridge


class SettingsTests(QtTestCase):
    def test_haptics_settings_persist_without_backend_and_retain_strength_when_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launch-mode"
            settings = deckthere_ui.Settings(path=path)
            settings.sleep_timer.stop()
            self.assertTrue(settings.hapticsEnabled)
            self.assertEqual(settings.hapticsStrength, "normal")
            settings.saveHapticsStrength("quiet")
            settings.toggleHaptics()
            restored = deckthere_ui.Settings(path=path)
            restored.sleep_timer.stop()
            self.assertFalse(restored.hapticsEnabled)
            self.assertEqual(restored.hapticsStrength, "quiet")
            restored.toggleHaptics()
            self.assertTrue(restored.hapticsEnabled)
            restored.saveHapticsStrength("invalid")
            self.assertEqual(restored.hapticsStrength, "quiet")
            self.assertIn("Could not save", restored.message)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"preferences": restored})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_settings.qml")))
            window = engine.rootObjects()[0]
            pump(0.1)
            items = {
                item.objectName(): item for item in walk(window.contentItem()) if item.objectName()
            }
            for name in ("haptics_strong", "hapticsToggle"):
                item = items[name]
                QTest.mouseClick(
                    window,
                    Qt.LeftButton,
                    Qt.NoModifier,
                    item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint(),
                )
            self.assertFalse(restored.hapticsEnabled)
            self.assertEqual(restored.hapticsStrength, "strong")
            self.assertEqual((path.parent / "haptics").read_text(), "0 strong\n")
            window.close()

    def test_auto_dim_toggles_are_independent_and_backend_confirmed(self):
        with Harness() as harness, tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bridge = self.bridge_for(harness)
            preferences = deckthere_ui.Settings(bridge, base / "launch-mode")
            self.assertTrue(pump(1, lambda: bridge.shared))
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"preferences": preferences})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_settings.qml")))
            window = engine.rootObjects()[0]
            pump(0.1)
            items = {
                item.objectName(): item for item in walk(window.contentItem()) if item.objectName()
            }

            def click(name):
                item = items[name]
                QTest.mouseClick(
                    window,
                    Qt.LeftButton,
                    Qt.NoModifier,
                    item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint(),
                )

            self.assertTrue(preferences.autoDimNow)
            self.assertTrue(preferences.autoDimLaunch)
            click("autoDimLaunch")
            self.assertFalse(preferences.autoDimLaunch)
            self.assertTrue(preferences.autoDimNow)
            self.assertEqual((base / "auto-dim").read_text(), "0\n")
            click("autoDimNow")
            self.assertTrue(pump(1, lambda: preferences.canDim and not preferences.autoDimNow))
            click("autoDimNow")
            self.assertTrue(pump(1, lambda: preferences.canDim and preferences.autoDimNow))
            self.assertEqual((base / "auto-dim").read_text(), "0\n")
            self.assertFalse((base / "launch-mode").exists())
            with patch.object(harness.brightness, "set_auto_dim", side_effect=OSError("mock")):
                click("autoDimNow")
                self.assertTrue(pump(1, lambda: preferences.canDim))
                self.assertTrue(preferences.autoDimNow)
                self.assertIn("Could not change", preferences.message)
            preferences.sleep_timer.stop()
            window.close()

    def test_sleep_choices_and_rendered_warning_cancel_without_startup_changes(self):
        policy = deckthere_ui.deckthere_sleep
        # Fresh CI machines may have less than five minutes of uptime. Offset
        # only the policy clock; keep the Qt event pump's real clock advancing.
        clock = SimpleNamespace(monotonic=lambda: time.monotonic() + 1000)
        with tempfile.TemporaryDirectory() as directory, patch.object(policy, "time", clock):
            base = Path(directory)
            preferences = deckthere_ui.Settings(path=base / "launch-mode")
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"preferences": preferences})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_settings.qml")))
            self.assertTrue(engine.rootObjects())
            window = engine.rootObjects()[0]
            pump(0.1)
            items = {
                item.objectName(): item for item in walk(window.contentItem()) if item.objectName()
            }
            self.assertEqual(
                {name for name in items if name.startswith("sleep_")},
                {"sleep_0", "sleep_5", "sleep_15", "sleep_30", "sleep_60"},
            )
            button = items["sleep_5"]
            QTest.mouseClick(
                window,
                Qt.LeftButton,
                Qt.NoModifier,
                button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint(),
            )
            self.assertEqual(preferences.sleepMinutes, 5)
            self.assertEqual(policy.read_minutes(base), 5)
            self.assertFalse((base / "launch-mode").exists())
            now = clock.monotonic()
            state = policy.initialize(base, now - 301)
            state.update(minutes=5, tick=now)
            policy.save_data(base / policy.STATE, state)
            policy.save_data(base / "sleep-activity", now - 301)

            def observe(now):
                return {"healthy": True, "activity": state["last"]}

            warning = policy.tick(base, now, observe)
            self.assertEqual(warning["status"], "warning")
            preferences.refreshSleep()
            banner = items["sleepWarningBanner"]
            self.assertTrue(
                pump(1, lambda: banner.isVisible() and (base / "sleep-warning-seen").exists())
            )
            self.assertGreaterEqual(banner.height(), 64)
            self.assertEqual(
                policy.read_data(base / "sleep-warning-seen")["warning"], warning["warning"]
            )
            QTest.mouseClick(
                window,
                Qt.LeftButton,
                Qt.NoModifier,
                banner.mapToScene(QPointF(banner.width() / 2, banner.height() / 2)).toPoint(),
            )
            self.assertEqual(policy.tick(base, clock.monotonic(), observe)["status"], "armed")
            preferences.refreshSleep()
            self.assertFalse(banner.isVisible())
            self.assertFalse((base / "launch-mode").exists())
            preferences.sleep_timer.stop()
            window.close()

    def test_dashboard_only_hides_keyboard_input_and_has_settings_and_quit(self):
        with Harness(keyboard=False) as harness, tempfile.TemporaryDirectory() as directory:
            bridge = self.bridge_for(harness)
            preferences = deckthere_ui.Settings(bridge, Path(directory) / "launch-mode")
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge, "preferences": preferences})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            self.assertTrue(engine.rootObjects())
            window = engine.rootObjects()[0]
            self.assertTrue(pump(1, lambda: bridge.connected))
            items = {
                item.objectName(): item for item in walk(window.contentItem()) if item.objectName()
            }
            self.assertEqual(items["keyboardToggle"].property("text"), "KEYBOARD NOT RUNNING")
            self.assertFalse(items["keyboardToggle"].isEnabled())
            self.assertTrue(items["holdQuit"].isVisible())
            self.assertEqual(items["layoutButton"].height(), items["settingsButton"].height())
            self.assertEqual(items["layoutButton"].height(), items["releaseKeys"].height())
            window.setProperty("keyboardOpen", True)
            pump(0.05)
            self.assertFalse(items["keypadTouch"].isEnabled())
            window.setProperty("settingsOpen", True)
            pump(0.1)
            items = {
                item.objectName(): item for item in walk(window.contentItem()) if item.objectName()
            }
            self.assertIn("settingsPanel", items)
            self.assertNotIn("startKeyboardDefault", items)
            session_button = items["sessionKeyboard"]
            separator = items["sessionSeparator"]
            self.assertEqual(
                session_button.property("label"), "Start keyboard for this session ONLY"
            )
            self.assertGreater(separator.width(), 0)
            self.assertGreater(separator.height(), 0)
            self.assertLess(
                items["default_gui"].mapToScene(QPointF(0, items["default_gui"].height())).y(),
                separator.mapToScene(QPointF(0, 0)).y(),
            )
            self.assertLess(
                separator.mapToScene(QPointF(0, separator.height())).y(),
                session_button.mapToScene(QPointF(0, 0)).y(),
            )
            button = items["default_terminal"]
            QTest.mouseClick(
                window,
                Qt.LeftButton,
                Qt.NoModifier,
                button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint(),
            )
            self.assertEqual(preferences.mode, "terminal")
            self.assertEqual(preferences.path.read_text().strip(), "terminal")
            self.assertFalse(harness.backend.stopping)
            self.assertFalse(bridge.keyboardEnabled)
            # The two independent actions can be clicked together, in either order.
            with patch.object(deckthere_backend, "Gadget", side_effect=FakeGadget):
                for control in (session_button, items["default_keyboard"]):
                    QTest.mouseClick(
                        window,
                        Qt.LeftButton,
                        Qt.NoModifier,
                        control.mapToScene(
                            QPointF(control.width() / 2, control.height() / 2)
                        ).toPoint(),
                    )
                self.assertTrue(pump(2, lambda: bridge.keyboardEnabled and not preferences.busy))
            self.assertEqual(preferences.path.read_text().strip(), "keyboard")
            self.assertEqual(
                session_button.property("label"), "Stop keyboard for this session ONLY"
            )
            QTest.mouseClick(
                window,
                Qt.LeftButton,
                Qt.NoModifier,
                session_button.mapToScene(
                    QPointF(session_button.width() / 2, session_button.height() / 2)
                ).toPoint(),
            )
            self.assertTrue(pump(2, lambda: not bridge.keyboardEnabled and not preferences.busy))
            self.assertEqual(
                session_button.property("label"), "Start keyboard for this session ONLY"
            )
            self.assertEqual(preferences.path.read_text().strip(), "keyboard")
            self.assertFalse(harness.backend.stopping)
            self.assertFalse(items["keypadTouch"].isEnabled())
            window.close()

    def test_session_start_and_stop_never_change_saved_choice(self):
        for saved in (None, "gui", "keyboard", "terminal"):
            with (
                self.subTest(saved=saved),
                Harness(keyboard=False) as harness,
                tempfile.TemporaryDirectory() as directory,
                patch.object(deckthere_backend, "Gadget", side_effect=FakeGadget) as factory,
            ):
                bridge = self.bridge_for(harness)
                preferences = deckthere_ui.Settings(bridge, Path(directory) / "launch-mode")
                self.assertTrue(pump(1, lambda: bridge.connected))
                if saved is not None:
                    preferences.save(saved)
                with patch.object(
                    preferences,
                    "save",
                    side_effect=AssertionError("Session controls must not save preferences"),
                ):
                    preferences.toggleKeyboard()
                    self.assertTrue(preferences.busy)
                    self.assertTrue(
                        pump(2, lambda: bridge.keyboardEnabled and not preferences.busy)
                    )
                    preferences.toggleKeyboard()
                    self.assertTrue(preferences.busy)
                    self.assertTrue(
                        pump(2, lambda: not bridge.keyboardEnabled and not preferences.busy)
                    )
                factory.assert_called_once_with()
                self.assertEqual(preferences.path.exists(), saved is not None)
                self.assertEqual(preferences.mode, saved or "gui")
                if saved is not None:
                    self.assertEqual(preferences.path.read_text().strip(), saved)
                bridge.drop()

    def test_failed_start_does_not_change_preference_or_stop_sharing(self):
        with (
            Harness(keyboard=False) as harness,
            tempfile.TemporaryDirectory() as directory,
            patch.object(deckthere_backend, "Gadget", side_effect=RuntimeError("mock failure")),
        ):
            bridge = self.bridge_for(harness)
            preferences = deckthere_ui.Settings(bridge, Path(directory) / "launch-mode")
            self.assertTrue(pump(1, lambda: bridge.connected))
            preferences.toggleKeyboard()
            self.assertTrue(pump(2, lambda: not preferences.busy))
            self.assertFalse(preferences.path.exists())
            self.assertIn("could not start", preferences.message)
            self.assertFalse(harness.backend.stopping)

    def test_failed_stop_keeps_session_control_available_for_retry(self):
        with Harness() as harness, tempfile.TemporaryDirectory() as directory:
            bridge = self.bridge_for(harness)
            preferences = deckthere_ui.Settings(bridge, Path(directory) / "launch-mode")
            self.assertTrue(pump(1, lambda: bridge.keyboardEnabled))
            with patch.object(harness.gadget, "close", side_effect=OSError("mock failure")):
                preferences.toggleKeyboard()
                self.assertTrue(pump(2, lambda: not preferences.busy))
                self.assertIn("could not stop", preferences.message)
                self.assertTrue(preferences.keyboardEnabled)
                self.assertTrue(preferences.canToggle)
                self.assertFalse(harness.backend.stopping)
                self.assertFalse(preferences.path.exists())
            preferences.toggleKeyboard()
            self.assertTrue(
                pump(2, lambda: not preferences.keyboardEnabled and not preferences.busy)
            )

    def test_standalone_terminal_settings_has_no_backend_or_live_keyboard(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(deckthere_ui, "Bridge") as bridge,
        ):
            preferences = deckthere_ui.Settings(path=Path(directory) / "launch-mode")
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"preferences": preferences})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_settings.qml")))
            self.assertTrue(engine.rootObjects())
            window = engine.rootObjects()[0]
            self.assertFalse(preferences.canToggle)
            preferences.toggleKeyboard()
            self.assertFalse(preferences.path.exists())
            preferences.save("gui")
            self.assertEqual(preferences.path.read_text().strip(), "gui")
            bridge.assert_not_called()
            window.close()


class UiBackendTests(QtTestCase):
    def test_bridge_connects_and_reports_backend_state(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.connected), "bridge never connected")
            self.assertTrue(pump(3, lambda: bridge.shared), "bridge never saw shared state")
            self.assertEqual(bridge.percent, 1)
            self.assertEqual(bridge.layout, "us")

    def test_volume_event_updates_visible_brightness_without_periodic_status(self):
        harness = Harness()
        volume = FakeVolume(harness.brightness)
        harness.backend.volume = volume
        with patch.object(deckthere_backend, "STATUS_INTERVAL", 60), harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            self.assertTrue(pump(3, lambda: bridge.shared and bridge.percent == 1))
            label = next(
                item
                for item in walk(window.contentItem())
                if item.property("text") == "Brightness 1%"
            )
            os.write(volume.writer, b"+")
            self.assertTrue(pump(1, lambda: label.property("text") == "Brightness 2%"))
            self.assertEqual(bridge.percent, 2)

    def test_typing_through_the_ui_reaches_the_usb_keyboard(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.press(4)
            self.assertEqual(harness.gadget.reports(1), [KEY_A])
            bridge.release(4)
            self.assertEqual(harness.gadget.reports(1), [bytes(8)])

    def test_latched_shift_travels_with_the_next_character(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.press(225)
            bridge.release(225)
            self.assertTrue(bridge.shiftActive)
            # Tapping a modifier is a momentary press on the wire, and it must be
            # released again rather than left down on the remote machine.
            self.assertEqual(
                harness.gadget.reports(2),
                [bytes([0b00000010, 0, 0, 0, 0, 0, 0, 0]), bytes(8)],
            )
            bridge.press(4)
            # Shift is re-applied for the next character, then cleared with it.
            reports = harness.gadget.reports(2)
            self.assertEqual(
                reports,
                [bytes([0b00000010, 0, 0, 0, 0, 0, 0, 0]), SHIFT_A],
            )
            bridge.release(4)
            self.assertFalse(bridge.shiftActive)

    def test_ctrl_stays_latched_across_several_keys(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.press(224)
            bridge.release(224)
            harness.gadget.reports(1)  # the momentary Ctrl press
            bridge.press(6)
            self.assertEqual(harness.gadget.reports(1)[0][0], 0b00000001)
            bridge.release(6)
            bridge.press(25)
            self.assertEqual(harness.gadget.reports(1)[0][0], 0b00000001)

    def test_layout_switch_changes_labels_and_reaches_the_backend(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.setLayout("de")
            self.assertEqual(bridge.layoutName, "German / Deutsch — QWERTZ")
            labels = [key["label"] for row in bridge.rows for key in row]
            self.assertIn("ö", labels)
            self.assertIn("z", labels)
            self.assertTrue(pump(3, lambda: harness.backend.layout == "de"))

    def test_clear_and_layout_changes_preserve_locally_tracked_caps(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.press(57)
            self.assertTrue(bridge.capsActive)
            bridge.clear()
            self.assertTrue(bridge.capsActive)
            bridge.setLayout("fr")
            self.assertTrue(bridge.capsActive)

    def test_korean_type1_ime_buttons_do_not_latch_as_modifiers(self):
        import select

        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.setLayout("ko-104")
            self.assertTrue(pump(3, lambda: harness.backend.layout == "ko-104"))
            pump(0.1)
            while select.select([harness.gadget.read_fd], [], [], 0)[0]:
                os.read(harness.gadget.read_fd, 4096)
            bridge.press(230)
            bridge.release(230)
            self.assertEqual(
                harness.gadget.reports(2), [bytes([64, 0, 0, 0, 0, 0, 0, 0]), bytes(8)]
            )
            self.assertFalse(bridge.altgrActive)
            bridge.press(4)
            self.assertEqual(harness.gadget.reports(1), [KEY_A])

    def test_clear_releases_held_keys_and_resets_modifiers(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.press(225)
            harness.gadget.reports(1)
            bridge.clear()
            self.assertFalse(bridge.shiftActive)
            self.assertEqual(harness.gadget.reports(1), [bytes(8)])

    def test_stopping_the_backend_is_reported_and_the_ui_recovers(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            self.assertTrue(pump(3, lambda: bridge.shared))
            bridge.stop()
            harness.thread.join(timeout=3)
            self.assertFalse(harness.thread.is_alive())
            # The bridge must drop the connection rather than silently pretend.
            self.assertTrue(pump(3, lambda: not bridge.connected))
            self.assertFalse(bridge.shared)

    def test_layout_metadata_matches_the_protocol(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            names = [entry["id"] for entry in bridge.layoutNames]
            self.assertEqual(names, list(deckthere_keyboard.LAYOUT_NAMES))
            self.assertGreaterEqual(len(names), 50)
            self.assertEqual(bridge.columns, 1000)

    def test_every_row_and_span_is_exposed_to_qml(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            rows = bridge.rows
            self.assertEqual(len(rows), 6)
            for row in rows:
                self.assertEqual(sum(key["span"] for key in row), bridge.columns)
                for key in row:
                    self.assertIn("code", key)
                    self.assertTrue(key["label"])


class QmlTests(QtTestCase):
    def test_compact_status_warning_and_responsive_keyboard_layout(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            self.assertTrue(pump(1, lambda: bridge.shared))
            window.showNormal()
            bridge.receive_dashboard(
                {
                    "clock": "12:34",
                    "battery": "84%",
                    "batteryState": "Not charging",
                    "clients": "192.0.2.1",
                    "adaptiveWarning": True,
                }
            )
            items = {
                item.objectName(): item for item in walk(window.contentItem()) if item.objectName()
            }
            self.assertFalse(items["compactStatus"].isVisible())
            window.setProperty("keyboardOpen", True)
            for width, height in ((1280, 800), (800, 600), (640, 800)):
                window.resize(width, height)
                pump(0.2)
                self.assertTrue(items["compactClock"].isVisible())
                self.assertEqual(items["compactClock"].property("text"), "12:34")
                self.assertEqual(items["compactClock"].property("color").name(), "#da8de8")
                self.assertEqual(items["compactBattery"].property("text"), "84%")
                self.assertTrue(items["compactClient"].isVisible())
                self.assertEqual(items["compactClient"].property("text"), "Client connected")
                self.assertTrue(items["adaptiveWarning"].isVisible())
                for name in ("compactClock", "compactBattery", "compactBatteryState"):
                    item = items[name]
                    if item.isVisible():
                        self.assertLessEqual(
                            item.mapToScene(QPointF(item.width(), 0)).x(),
                            items["keyboardToggle"].x() - 8,
                        )
                for name in ("brightnessLabel", "adaptiveWarning", "compactClient"):
                    item = items[name]
                    self.assertGreater(item.width(), 0)
                    self.assertLessEqual(
                        item.mapToScene(QPointF(item.width(), 0)).x(),
                        items["settingsButton"].mapToScene(QPointF(0, 0)).x() - 8,
                    )
                if width == 640:
                    self.assertFalse(items["compactBatteryState"].isVisible())
            for clients, label in (("None", "No client"), ("Unavailable", "Client unknown")):
                bridge.receive_dashboard({"clients": clients, "adaptiveWarning": False})
                pump(0.1)
                self.assertEqual(items["compactClient"].property("text"), label)
                self.assertFalse(items["adaptiveWarning"].isVisible())
            window.close()

    def test_quit_fill_pixels_stay_inside_rounded_button(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            window.showNormal()
            window.resize(1280, 800)
            pump(0.2)
            button = next(
                item for item in walk(window.contentItem()) if item.objectName() == "holdQuit"
            )

            def color(image, x, y):
                point = button.mapToScene(QPointF(x, y)) * image.devicePixelRatio()
                return image.pixelColor(int(point.x()), int(point.y())).name()

            baseline = window.grabWindow()
            self.assertFalse(baseline.isNull())
            for progress in (0.01, 0.5, 1.0):
                button.setProperty("progress", progress)
                pump(0.15)
                image = window.grabWindow()
                for x, y in (
                    (2, 2),
                    (2, button.height() - 3),
                    (button.width() - 3, 2),
                    (button.width() - 3, button.height() - 3),
                ):
                    self.assertEqual(color(image, x, y), color(baseline, x, y))
                if progress >= 0.5:
                    self.assertEqual(
                        color(image, button.width() * 0.25, button.height() * 0.75), "#a13333"
                    )
            window.close()

    def test_qml_loads_without_errors_or_warnings(self):
        messages = []

        def handler(mode, context, message):
            del mode, context
            messages.append(message)

        previous = qInstallMessageHandler(handler)
        try:
            with Harness() as harness:
                bridge = self.bridge_for(harness)
                engine = QQmlApplicationEngine()
                engine.setInitialProperties({"deckthere": bridge})
                engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
                self.assertTrue(engine.rootObjects(), "QML produced no root object")
                pump(0.4)
                roots = engine.rootObjects()
        finally:
            qInstallMessageHandler(previous)
        problems = [
            message
            for message in messages
            if "TypeError" in message
            or "ReferenceError" in message
            or "is not defined" in message
            or "Unable to assign" in message
            or "Cannot anchor" in message
            or "Required property" in message
        ]
        self.assertEqual(problems, [])
        self.assertTrue(roots)

    def test_the_keyboard_renders_a_keycap_per_key_and_toggles_visibility(self):
        # A silently-empty delegate model still loads cleanly and reports no QML
        # errors, so assert on real rendered items rather than on messages.
        # QML items are not reachable via findChildren; walk the visual tree.
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            self.assertTrue(engine.rootObjects(), "QML produced no root object")
            window = engine.rootObjects()[0]
            content = window.property("contentItem")
            self.assertIsInstance(content, QQuickItem)

            def rendered(name):
                pump(0.2)
                return [item for item in walk(content) if item.objectName() == name]

            self.assertEqual(rendered("brandTitle")[0].property("text"), "DeckThere")
            keypad = rendered("keypad")
            self.assertEqual(len(keypad), 1)
            # Hidden, not absent: delegate models are built either way.
            self.assertFalse(keypad[0].property("visible"))

            expected = sum(len(row) for row in deckthere_keyboard.layout_grid("us"))
            self.assertEqual(len(rendered("keycap")), expected)
            # Counting items is not enough: an undefined divisor once made every
            # key zero-width, so a fully invisible keyboard still passed.
            caps = rendered("keycap")
            self.assertTrue(
                all(item.width() > 0 for item in caps), "every keycap must have a real width"
            )
            self.assertTrue(
                all(item.height() > 0 for item in caps), "every keycap must have a real height"
            )
            self.assertEqual(window.property("columns"), bridge.columns)
            labels = {item.property("text") for item in rendered("keycapLabel")}
            self.assertIn("a", labels)
            self.assertIn("Space", labels)

            toggle = rendered("keyboardToggle")[0]
            position = toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
            self.assertTrue(rendered("keypad")[0].property("visible"))
            self.assertEqual(len(rendered("keycap")), expected)

            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
            self.assertFalse(rendered("keypad")[0].property("visible"))

    def test_real_touch_events_reference_count_and_hide_releases_keys(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            self.assertTrue(pump(3, lambda: bridge.shared))
            window.setProperty("keyboardOpen", True)
            pump(0.2)
            label = next(
                item
                for item in walk(window.contentItem())
                if item.objectName() == "keycapLabel" and item.property("text") == "a"
            )
            position = label.mapToScene(QPointF(label.width() / 2, label.height() / 2)).toPoint()
            # Focus changes can send an initial clear. Drain only the fixture pipe.
            import select

            while select.select([harness.gadget.read_fd], [], [], 0)[0]:
                os.read(harness.gadget.read_fd, 4096)

            def key_color():
                # Status/legend updates rebuild delegates; inspect the live keycap.
                return next(
                    item.property("color").name()
                    for item in walk(window.contentItem())
                    if item.objectName() == "keycap" and item.property("keyCode") == 4
                )

            self.assertEqual(key_color(), "#1b2632")
            device = QTest.createTouchDevice()
            sequence = QTest.touchEvent(window, device)
            sequence.press(0, position, window).commit()
            pump(0.1)
            self.assertEqual(harness.gadget.reports(1), [KEY_A])
            self.assertEqual(key_color(), "#2f6ea8")
            sequence.stationary(0).press(1, position, window).commit()
            pump(0.1)
            self.assertFalse(select.select([harness.gadget.read_fd], [], [], 0.1)[0])
            self.assertEqual(key_color(), "#2f6ea8")
            sequence.release(0, position, window).stationary(1).commit()
            pump(0.1)
            self.assertFalse(select.select([harness.gadget.read_fd], [], [], 0.1)[0])
            self.assertEqual(key_color(), "#2f6ea8")
            sequence.release(1, position, window).commit()
            pump(0.1)
            self.assertEqual(harness.gadget.reports(1), [bytes(8)])
            self.assertEqual(key_color(), "#1b2632")
            sequence.press(0, position, window).commit()
            pump(0.1)
            self.assertEqual(harness.gadget.reports(1), [KEY_A])
            self.assertEqual(key_color(), "#2f6ea8")
            window.setProperty("keyboardOpen", False)
            pump(0.1)
            self.assertEqual(harness.gadget.reports(1), [bytes(8)])
            self.assertEqual(key_color(), "#1b2632")
            sequence.release(0, position, window).commit()

    def test_touch_highlights_follow_slides_multiple_keys_and_modal_cleanup(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            self.assertTrue(pump(3, lambda: bridge.shared))
            window.setProperty("keyboardOpen", True)
            pump(0.1)

            def cap(code):
                return next(
                    item
                    for item in walk(window.contentItem())
                    if item.objectName() == "keycap" and item.property("keyCode") == code
                )

            def position(code):
                item = cap(code)
                return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()

            def lit(*codes):
                self.assertEqual(
                    {code for code in (4, 5, 6) if cap(code).property("color").name() == "#2f6ea8"},
                    set(codes),
                )

            a, b, c = (position(code) for code in (4, 5, 6))
            device = QTest.createTouchDevice()
            sequence = QTest.touchEvent(window, device)
            sequence.press(0, a, window).press(1, b, window).commit()
            pump(0.1)
            lit(4, 5)
            sequence.move(0, c, window).stationary(1).commit()
            pump(0.1)
            lit(5, 6)
            sequence.release(0, c, window).stationary(1).commit()
            pump(0.1)
            lit(5)
            window.setProperty("layoutChooserOpen", True)
            pump(0.1)
            lit()
            sequence.release(1, b, window).commit()
            window.setProperty("layoutChooserOpen", False)
            # Modifier one-shots and Caps keep their existing latched highlights.
            for code in (225, 57):
                bridge.press(code)
                bridge.release(code)
                pump(0.1)
                self.assertEqual(cap(code).property("color").name(), "#2f6ea8")
            window.close()

    def test_layout_chooser_opens_filters_scrolls_selects_and_closes(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            self.assertTrue(pump(3, lambda: bridge.shared))

            def item(name):
                return next(
                    child for child in walk(window.contentItem()) if child.objectName() == name
                )

            def click(target):
                position = target.mapToScene(
                    QPointF(target.width() / 2, target.height() / 2)
                ).toPoint()
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
                pump(0.1)

            self.assertFalse(item("layoutChooser").isVisible())
            self.assertFalse(
                any(
                    child.objectName().startswith("layoutChoice-")
                    for child in walk(window.contentItem())
                )
            )
            click(item("layoutButton"))
            self.assertTrue(item("layoutChooser").isVisible())
            choices = [
                child
                for child in walk(window.contentItem())
                if child.objectName().startswith("layoutChoice-")
            ]
            self.assertEqual(len(choices), len(deckthere_keyboard.CATALOG))
            self.assertTrue(all(child.width() > 100 and child.height() > 40 for child in choices))
            self.assertGreater(
                item("layoutList").property("contentHeight"), item("layoutList").height()
            )
            click(item("layoutSearch"))
            QTest.keyClick(window, Qt.Key_Z)
            QTest.keyClick(window, Qt.Key_H)
            pump(0.1)
            self.assertEqual(window.property("layoutFilter"), "zh")
            choices = [
                child
                for child in walk(window.contentItem())
                if child.objectName().startswith("layoutChoice-")
            ]
            self.assertGreaterEqual(len(choices), 10)
            self.assertTrue(
                all(child.objectName().startswith("layoutChoice-zh") for child in choices)
            )
            target = item("layoutChoice-zh-jyutping")
            listing = item("layoutList")
            listing.setProperty(
                "contentY",
                max(0, min(target.y(), listing.property("contentHeight") - listing.height())),
            )
            pump(0.1)
            click(target)
            self.assertTrue(pump(3, lambda: harness.backend.layout == "zh-jyutping"))
            self.assertEqual(bridge.layout, "zh-jyutping")
            self.assertIn("PC", item("layoutDetails").property("text"))
            click(item("layoutDone"))
            self.assertFalse(item("layoutChooser").isVisible())

    def test_all_catalog_layouts_have_real_key_geometry_when_open(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            self.assertTrue(pump(3, lambda: bridge.shared))
            window.setProperty("keyboardOpen", True)
            for layout in deckthere_keyboard.CATALOG:
                with self.subTest(layout=layout):
                    bridge.setLayout(layout)
                    self.assertTrue(pump(3, lambda: harness.backend.layout == layout))
                    pump(0.02)
                    caps = [
                        child
                        for child in walk(window.contentItem())
                        if child.objectName() == "keycap"
                    ]
                    self.assertEqual(
                        len(caps), sum(len(row) for row in deckthere_keyboard.layout_rows(layout))
                    )
                    self.assertTrue(all(child.width() > 0 and child.height() > 0 for child in caps))

    def test_quit_requires_an_uninterrupted_two_second_hold(self):
        with Harness() as harness:
            bridge = self.bridge_for(harness)
            engine = QQmlApplicationEngine()
            engine.setInitialProperties({"deckthere": bridge})
            engine.load(QUrl.fromLocalFile(str(ROOT / "deckthere_ui.qml")))
            window = engine.rootObjects()[0]
            # Observe the quit request without quitting the shared test application.
            engine.quit.disconnect()
            quits = []
            engine.quit.connect(lambda: quits.append(True))
            self.assertTrue(pump(3, lambda: bridge.shared))
            button = next(
                item for item in walk(window.contentItem()) if item.objectName() == "holdQuit"
            )
            position = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            self.assertEqual(button.property("holdMilliseconds"), 2000)
            text = "\n".join(
                str(item.property("text") or "") for item in walk(window.contentItem())
            )
            self.assertIn("HOLD 2s TO QUIT", text)
            self.assertNotIn("corner", text.lower())

            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
            pump(2.2)
            self.assertEqual(quits, [])
            self.assertTrue(harness.thread.is_alive())

            QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, position)
            pump(0.3)
            outside = button.mapToScene(QPointF(-20, button.height() / 2)).toPoint()
            QTest.mouseMove(window, outside)
            pump(2.2)
            self.assertEqual(quits, [])
            QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, outside)

            QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, position)
            pump(1)
            self.assertEqual(quits, [])
            self.assertTrue(pump(2, lambda: bool(quits)))
            self.assertEqual(quits, [True])
            QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, position)
            harness.thread.join(timeout=3)
            self.assertFalse(harness.thread.is_alive())

    def test_qml_declares_the_bridge_as_a_required_property(self):
        # Context properties are cleared before the object tree is destroyed,
        # which makes every binding re-evaluate against a null at shutdown.
        source = (ROOT / "deckthere_ui.qml").read_text()
        self.assertIn("required property var deckthere", source)
        self.assertNotIn("setContextProperty", (ROOT / "deckthere_ui.py").read_text())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class PlatformTests(unittest.TestCase):
    def test_wayland_is_preferred_when_nothing_was_requested(self):
        environment = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        self.assertTrue(deckthere_ui.prefer_wayland(environment))
        self.assertEqual(environment["QT_QPA_PLATFORM"], "wayland")

    def test_an_explicit_platform_always_wins(self):
        for platform in ("offscreen", "xcb", "wayland", "minimal"):
            with self.subTest(platform=platform):
                environment = {"WAYLAND_DISPLAY": "wayland-0", "QT_QPA_PLATFORM": platform}
                self.assertFalse(deckthere_ui.prefer_wayland(environment))
                self.assertEqual(environment["QT_QPA_PLATFORM"], platform)

    def test_nothing_changes_without_a_wayland_session(self):
        for environment in ({}, {"DISPLAY": ":0"}, {"WAYLAND_DISPLAY": ""}):
            with self.subTest(environment=environment):
                before = dict(environment)
                self.assertFalse(deckthere_ui.prefer_wayland(environment))
                self.assertEqual(environment, before)

    def test_wayland_is_chosen_before_qt_picks_a_backend(self):
        # Setting QT_QPA_PLATFORM after QGuiApplication exists has no effect.
        source = (ROOT / "deckthere_ui.py").read_text()
        self.assertLess(source.index("prefer_wayland()"), source.index("QGuiApplication(sys.argv"))


if __name__ == "__main__":
    unittest.main()
