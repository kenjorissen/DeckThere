"""Gamescope property tests: no real compositor, input, or Steam changes."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import deckthere_touch as touch  # noqa: E402


class NativeTouchTests(unittest.TestCase):
    def setUp(self):
        self.mode, self.app, self.window, self.pid = 1, 123, 456, 789
        self.writes = []
        self.identity = 42
        guards = [
            patch.dict(
                os.environ,
                {"XDG_CURRENT_DESKTOP": "gamescope", "DISPLAY": ":1", "SteamAppId": "123"},
                clear=True,
            ),
            patch.object(touch.idle.os, "geteuid", return_value=1000),
            patch.object(touch.idle, "compositor", return_value=(10, 42)),
            patch.object(touch.idle, "displays", return_value=[":0"]),
            patch.object(touch.idle, "read_counter", return_value=0),
            patch.object(touch.idle, "process_identity", side_effect=lambda _: self.identity),
            patch.object(touch.idle, "command", side_effect=self.command),
            patch.object(touch.NativeTouch, "log"),
        ]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)

    def command(self, args, display=None):
        if "-set" in args:
            self.assertEqual(display, ":0")
            self.mode = int(args[-1])
            self.writes.append(self.mode)
            return ""
        if "-id" in args:
            self.assertEqual(display, ":1")
            return f"_NET_WM_PID(CARDINAL) = {self.pid}\n"
        return f"{touch.MODE}(CARDINAL) = {self.mode}\n{touch.APP}(CARDINAL) = {self.app}\n{touch.WINDOW}(CARDINAL) = {self.window}\n"

    def test_focus_overlay_reentry_and_cleanup(self):
        guard = touch.NativeTouch.start(789)
        guard.tick()
        guard.tick()
        self.assertEqual(self.writes, [4])
        self.app = 769  # Steam overlay: base window may still be DeckThere.
        guard.tick()
        self.assertEqual(self.mode, 1)
        self.app = 123
        guard.tick()
        guard.close()
        self.assertEqual(self.writes, [4, 1, 4, 1])

    def test_never_changes_other_process_with_same_app_id(self):
        self.pid = 999
        guard = touch.NativeTouch.start(789)
        guard.tick()
        self.assertEqual(self.writes, [])

    def test_external_change_is_preserved_on_focus_loss_and_exit(self):
        for close in (False, True):
            self.mode, self.app = 1, 123
            guard = touch.NativeTouch.start(789)
            guard.tick()
            self.mode, self.app = 2, 769
            guard.close() if close else guard.tick()
            self.assertEqual(self.mode, 2)

    def test_steam_rebind_restores_latest_mode(self):
        guard = touch.NativeTouch.start(789)
        guard.tick()
        self.mode = 3
        guard.tick()
        self.assertEqual(self.mode, 4)
        guard.close()
        self.assertEqual(self.mode, 3)

    def test_existing_passthrough_is_not_owned(self):
        self.mode = 4
        guard = touch.NativeTouch.start(789)
        guard.tick()
        guard.close()
        self.assertEqual(self.writes, [])

    def test_session_replacement_never_writes_to_new_compositor(self):
        guard = touch.NativeTouch.start(789)
        guard.tick()
        self.identity = 43
        guard.tick()
        guard.close()
        self.assertEqual(self.writes, [4])

    def test_missing_properties_disable_without_creating_them(self):
        with patch.object(
            touch.idle, "command", return_value="STEAM_TOUCH_CLICK_MODE: not found.\n"
        ):
            guard = touch.NativeTouch.start(789)
            self.assertIsNone(guard.target)
        self.assertEqual(self.writes, [])

    def test_timeout_after_acquisition_restores_and_disables(self):
        guard = touch.NativeTouch.start(789)
        guard.tick()
        with patch.object(guard, "owns_focus", side_effect=touch.idle.IdleError("timeout")):
            guard.tick()
        self.assertEqual(self.mode, 1)
        self.assertIsNone(guard.target)

    def test_desktop_optout_wayland_and_missing_app_are_noops(self):
        for change in (
            {"XDG_CURRENT_DESKTOP": "KDE"},
            {"DECKTHERE_DISABLE_NATIVE_TOUCH": "1"},
            {"WAYLAND_DISPLAY": "wayland-0"},
            {"SteamAppId": ""},
        ):
            with patch.dict(os.environ, change), patch.object(touch.idle, "command") as command:
                guard = touch.NativeTouch.start(789)
                guard.tick()
                guard.close()
                command.assert_not_called()

    def test_empty_focus_is_valid_but_unknown_mode_is_not(self):
        self.assertEqual(touch.cardinal(f"{touch.APP}(CARDINAL) = \n", touch.APP, empty=True), 0)
        self.mode = 99
        self.assertIsNone(touch.NativeTouch.start(789).target)


if __name__ == "__main__":
    unittest.main()
