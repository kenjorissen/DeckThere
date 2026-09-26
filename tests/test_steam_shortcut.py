import importlib.util
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "shortcut", Path(__file__).resolve().parents[1] / "tools/steam-shortcut.py"
)
shortcut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shortcut)


def entries(data):
    return shortcut.decode(data)[0][2]


def fields(entry):
    return {key: value for _, key, value in entry[2]}


def install_launcher(home):
    install_dir = home / ".local/share/deckthere"
    install_dir.mkdir(parents=True)
    for name in ("deckthere.sh", "deckthere-gui.sh", "deckthere-launch.sh"):
        launcher = install_dir / name
        launcher.write_text("#!/bin/bash\nexit 0\n")
        launcher.chmod(0o755)
    return install_dir


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        # Exercise a terminal-like environment even in CI. Missing prompt/process
        # mocks must fail immediately, never block for input or launch real Steam.
        guards = (
            patch.object(shortcut.sys.stdin, "isatty", return_value=True),
            patch(
                "builtins.input",
                side_effect=AssertionError(
                    "Unexpected interactive input: mock the prompt in this test"
                ),
            ),
            patch.object(
                shortcut.subprocess,
                "run",
                side_effect=AssertionError("Unexpected subprocess.run: mock it in this test"),
            ),
            patch.object(
                shortcut.subprocess,
                "Popen",
                side_effect=AssertionError("Unexpected subprocess.Popen: mock it in this test"),
            ),
        )
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)

    def test_unmocked_input_and_process_launch_fail_fast(self):
        with self.assertRaisesRegex(AssertionError, "Unexpected interactive input"):
            input("This must never reach a real terminal")
        with self.assertRaisesRegex(AssertionError, "Unexpected subprocess.run"):
            shortcut.subprocess.run(["steam", "-shutdown"])
        with self.assertRaisesRegex(AssertionError, "Unexpected subprocess.Popen"):
            shortcut.subprocess.Popen(["steam"])

    def test_new_and_idempotent(self):
        path = Path("/home/deck/my deckthere")
        data = shortcut.update(b"", path)
        self.assertEqual(shortcut.update(data, path), data)
        self.assertEqual(len(entries(data)), 1)
        values = fields(entries(data)[0])
        self.assertEqual(values[b"appname"], b"DeckThere")
        self.assertEqual(
            values[b"appid"],
            struct.pack("<I", zlib.crc32(b'"/usr/bin/env"DeckThere') | 0x80000000),
        )
        self.assertEqual(values[b"exe"], b'"/usr/bin/env"')
        self.assertEqual(
            values[b"LaunchOptions"],
            b'-u LD_PRELOAD "/home/deck/my deckthere/deckthere-launch.sh"',
        )
        self.assertEqual(values[b"AllowOverlay"], struct.pack("<I", 1))

    def test_bundled_icon_fills_empty_field_but_preserves_custom_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory)
            icon = installed / "artwork/icon.png"
            icon.parent.mkdir()
            icon.write_bytes(b"fixture icon")
            data = shortcut.update(b"", installed)
            self.assertEqual(fields(entries(data)[0])[b"icon"], str(icon).encode())
            self.assertEqual(shortcut.update(data, installed), data)
            root = shortcut.decode(data)
            entry = root[0][2][0][2]
            entry[:] = [
                (kind, key, b"/custom/icon.ico" if key == b"icon" else value)
                for kind, key, value in entry
            ]
            custom = shortcut.update(shortcut.encode(root), installed)
            self.assertEqual(fields(entries(custom)[0])[b"icon"], b"/custom/icon.ico")

    def test_library_art_uses_saved_appid_and_never_overwrites_existing_art(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / "installed"
            assets = installed / "artwork"
            assets.mkdir(parents=True)
            for name in ("portrait", "landscape", "hero", "logo"):
                (assets / f"{name}.png").write_bytes(name.encode())
            root = shortcut.decode(shortcut.update(b"", installed))
            entry = root[0][2][0][2]
            entry[:] = [
                (kind, key, struct.pack("<I", 123) if key == b"appid" else value)
                for kind, key, value in entry
            ]
            data = shortcut.encode(root)
            config = Path(directory) / "config"
            grid = config / "grid"
            grid.mkdir(parents=True)
            (grid / "123p.jpg").write_bytes(b"custom portrait")
            (grid / "123_hero.png").symlink_to(grid / "absent-custom-hero")
            (grid / "999p.png").write_bytes(b"another game")
            self.assertEqual(shortcut.install_artwork(data, installed, config), 2)
            self.assertEqual((grid / "123.png").read_bytes(), b"landscape")
            self.assertEqual((grid / "123_logo.png").read_bytes(), b"logo")
            self.assertEqual((grid / "123p.jpg").read_bytes(), b"custom portrait")
            self.assertTrue((grid / "123_hero.png").is_symlink())
            self.assertEqual((grid / "999p.png").read_bytes(), b"another game")
            self.assertEqual(shortcut.install_artwork(data, installed, config), 0)
            self.assertFalse(list(grid.glob(".deckthere-artwork-*")))

    def test_adopts_manual_entry_and_preserves_others(self):
        other = (
            0,
            b"0",
            [
                shortcut.text("appname", "Other"),
                (7, b"custom", b"12345678"),
                (3, b"float", b"abcd"),
            ],
        )
        manual = (
            0,
            b"1",
            [
                shortcut.text("appname", "env"),
                shortcut.text(
                    "LaunchOptions",
                    "-u LD_PRELOAD konsole --fullscreen -e /home/deck/deckthere/deckthere.sh",
                ),
                shortcut.number("appid", 123),
                shortcut.text("icon", "/my/art.png"),
                (0, b"tags", [shortcut.text("0", "Controllers")]),
            ],
        )
        data = shortcut.encode([(0, b"shortcuts", [other, manual])])
        updated = entries(shortcut.update(data, Path("/new/deckthere")))
        self.assertEqual(len(updated), 2)
        self.assertEqual(updated[0], other)
        values = fields(updated[1])
        self.assertEqual(values[b"appname"], b"DeckThere")
        self.assertEqual(values[b"appid"], struct.pack("<I", 123))
        self.assertEqual(values[b"icon"], b"/my/art.png")
        self.assertEqual(values[b"tags"], manual[2][-1][2])

    def test_name_matching_preserves_id_and_updates_install_path(self):
        for name in ("DeckThere", "deckthere"):
            with self.subTest(name=name):
                data = shortcut.encode(
                    [
                        (
                            0,
                            b"shortcuts",
                            [
                                (
                                    0,
                                    b"0",
                                    [
                                        shortcut.text("appname", name),
                                        shortcut.number("appid", 123),
                                        shortcut.text("icon", "/art.png"),
                                    ],
                                )
                            ],
                        )
                    ]
                )
                result = shortcut.update(data, Path("/home/deck/deckthere"))
                self.assertEqual(len(entries(result)), 1)
                values = fields(entries(result)[0])
                self.assertEqual(values[b"appname"], b"DeckThere")
                self.assertEqual(values[b"appid"], struct.pack("<I", 123))
                self.assertEqual(values[b"StartDir"], b'"/home/deck/deckthere"')
                self.assertIn(b"/home/deck/deckthere/deckthere-launch.sh", values[b"LaunchOptions"])
                self.assertEqual(values[b"icon"], b"/art.png")
                self.assertEqual(shortcut.update(result, Path("/home/deck/deckthere")), result)

    def test_shortcut_uses_installed_launcher_after_checkout_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            account = home / ".local/share/Steam/userdata/123"
            account.mkdir(parents=True)
            checkout = home / "custom folder/deckthere"
            checkout.mkdir(parents=True)
            script = checkout / "steam-shortcut.py"
            installed = install_launcher(home)
            checkout.rmdir()  # The original script location no longer exists.
            with (
                patch.object(shortcut, "__file__", str(script)),
                patch.object(shortcut.Path, "home", return_value=home),
                patch.object(shortcut.os, "geteuid", return_value=1000),
                patch.object(shortcut, "steam_running", return_value=False),
                patch.object(shortcut, "offer_start_steam"),
                patch("sys.argv", [str(script)]),
            ):
                shortcut.main()
            values = fields(entries((account / "config/shortcuts.vdf").read_bytes())[0])
            self.assertEqual(values[b"StartDir"], f'"{installed}"'.encode())
            self.assertIn(f'"{installed}/deckthere-launch.sh"'.encode(), values[b"LaunchOptions"])
            self.assertNotIn(str(checkout).encode(), values[b"LaunchOptions"])

    def test_missing_installed_launcher_does_not_close_steam(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".local/share/Steam/userdata/123").mkdir(parents=True)
            with (
                patch.object(shortcut.Path, "home", return_value=home),
                patch.object(shortcut.os, "geteuid", return_value=1000),
                patch("sys.argv", ["steam-shortcut.py"]),
                patch.object(shortcut, "ensure_steam_closed") as close,
                patch.object(shortcut, "save") as save,
            ):
                with self.assertRaises(SystemExit):
                    shortcut.main()
                close.assert_not_called()
                save.assert_not_called()

    def test_switching_modes_updates_one_shortcut_and_preserves_appid(self):
        path = Path("/home/deck/my deckthere")
        terminal = shortcut.update(b"", path, "terminal")
        keyboard = shortcut.update(terminal, path, "keyboard")
        self.assertEqual(len(entries(keyboard)), 1)
        before, after = fields(entries(terminal)[0]), fields(entries(keyboard)[0])
        self.assertEqual(before[b"appid"], after[b"appid"])
        self.assertEqual(after[b"appname"], b"DeckThere")
        self.assertEqual(after[b"LaunchOptions"], before[b"LaunchOptions"])
        self.assertNotIn(b" --keyboard", after[b"LaunchOptions"])
        self.assertNotIn(b" --terminal", after[b"LaunchOptions"])
        self.assertEqual(shortcut.update(keyboard, path, "terminal"), terminal)
        with self.assertRaises(ValueError):
            shortcut.update(keyboard, path, "--arbitrary-command")

    def test_rejects_duplicates(self):
        data = shortcut.encode(
            [
                (
                    0,
                    b"shortcuts",
                    [
                        (0, str(i).encode(), [shortcut.text("appname", "DeckThere")])
                        for i in range(2)
                    ],
                )
            ]
        )
        with self.assertRaises(ValueError):
            shortcut.update(data, Path("/deckthere"))

    def test_rejects_malformed_data(self):
        for data in (
            b"\0shortcuts\0",
            b"\x05bad\0\x08",
            b"\x08junk",
            b"\x02x\0a",
            b"\x01unterminated",
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                shortcut.decode(data)

    def test_rejects_unsafe_launch_path(self):
        with self.assertRaises(ValueError):
            shortcut.update(b"", Path("/home/deck/$HOME"))

    def test_backup_and_atomic_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shortcuts.vdf"
            path.write_bytes(b"original")
            shortcut.save(path, b"new")
            self.assertEqual(path.read_bytes(), b"new")
            backups = list(path.parent.glob("shortcuts.vdf.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"original")
            self.assertFalse(list(path.parent.glob(".deckthere-shortcuts-*")))

    def test_refuses_running_steam(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".local/share/Steam/userdata/123").mkdir(parents=True)
            install_launcher(home)
            with (
                patch.object(shortcut.Path, "home", return_value=home),
                patch("sys.argv", ["steam-shortcut.py"]),
                patch.object(shortcut.os, "geteuid", return_value=1000),
                patch.object(shortcut, "steam_running", return_value=True) as running,
                patch.object(shortcut.sys.stdin, "isatty", return_value=False),
                patch.object(shortcut, "save") as save,
            ):
                with self.assertRaises(SystemExit):
                    shortcut.main()
                running.assert_called_once()
                save.assert_not_called()

    def test_shutdown_declined(self):
        with (
            patch.object(shortcut, "steam_running", return_value=True),
            patch.object(shortcut.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="n"),
            patch.object(shortcut.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "left running"):
                shortcut.ensure_steam_closed()
            run.assert_not_called()

    def test_noninteractive_does_not_shutdown(self):
        with (
            patch.object(shortcut, "steam_running", return_value=True),
            patch.object(shortcut.sys.stdin, "isatty", return_value=False),
            patch.object(shortcut.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "Fully exit Steam"):
                shortcut.ensure_steam_closed()
            run.assert_not_called()

    def test_graceful_shutdown_waits_for_exit(self):
        with (
            patch.object(shortcut, "steam_running", side_effect=[True, True, False]),
            patch.object(shortcut.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="yes"),
            patch.object(shortcut.shutil, "which", return_value="/usr/bin/steam"),
            patch.object(shortcut.subprocess, "run") as run,
            patch.object(shortcut.time, "sleep") as sleep,
        ):
            shortcut.ensure_steam_closed()
            self.assertEqual(run.call_args.args[0], ["/usr/bin/steam", "-shutdown"])
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            sleep.assert_called_once_with(0.5)

    def test_shutdown_timeout(self):
        with (
            patch.object(shortcut, "steam_running", return_value=True),
            patch.object(shortcut.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="y"),
            patch.object(shortcut.shutil, "which", return_value="/usr/bin/steam"),
            patch.object(shortcut.subprocess, "run"),
            patch.object(shortcut.time, "monotonic", side_effect=[0, 31]),
        ):
            with self.assertRaisesRegex(ValueError, "did not exit"):
                shortcut.ensure_steam_closed()

    def test_shutdown_command_failure(self):
        with (
            patch.object(shortcut, "steam_running", return_value=True),
            patch.object(shortcut.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="y"),
            patch.object(shortcut.shutil, "which", return_value="/usr/bin/steam"),
            patch.object(
                shortcut.subprocess, "run", side_effect=subprocess.TimeoutExpired("steam", 15)
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Could not request"):
                shortcut.ensure_steam_closed()

    def test_reopen_requires_confirmation(self):
        with (
            patch.object(shortcut.sys.stdin, "isatty", return_value=True),
            patch.object(shortcut, "steam_running", return_value=False),
            patch("builtins.input", return_value="n"),
            patch.object(shortcut.subprocess, "Popen") as popen,
        ):
            shortcut.offer_start_steam()
            popen.assert_not_called()

    def test_reopen_detaches_steam(self):
        with (
            patch.object(shortcut.sys.stdin, "isatty", return_value=True),
            patch.object(shortcut, "steam_running", return_value=False),
            patch("builtins.input", return_value="yes"),
            patch.object(shortcut.shutil, "which", return_value="/usr/bin/steam"),
            patch.object(shortcut.subprocess, "Popen") as popen,
        ):
            shortcut.offer_start_steam()
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/steam"])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_reopen_noninteractive_is_skipped(self):
        with (
            patch.object(shortcut.sys.stdin, "isatty", return_value=False),
            patch.object(shortcut.subprocess, "Popen") as popen,
        ):
            shortcut.offer_start_steam()
            popen.assert_not_called()

    def test_check_does_not_stop_or_launch_steam(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            account = home / ".local/share/Steam/userdata/123"
            account.mkdir(parents=True)
            with (
                patch.object(shortcut.Path, "home", return_value=home),
                patch.object(shortcut.os, "geteuid", return_value=1000),
                patch("sys.argv", ["steam-shortcut.py", "--check"]),
                patch.object(shortcut, "ensure_steam_closed") as close,
                patch.object(shortcut, "offer_start_steam") as start,
                patch.object(shortcut, "save") as save,
            ):
                shortcut.main()
                close.assert_not_called()
                start.assert_not_called()
                save.assert_not_called()
                self.assertFalse((account / "config").exists())

    def test_multiple_accounts_requires_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            userdata = home / ".local/share/Steam/userdata"
            install_launcher(home)
            for account in ("123", "456"):
                (userdata / account / "config").mkdir(parents=True)
            with (
                patch.object(shortcut.Path, "home", return_value=home),
                patch.object(shortcut.os, "geteuid", return_value=1000),
                patch.object(shortcut, "steam_running", return_value=False),
                patch.object(shortcut, "offer_start_steam") as offer_start,
            ):
                with patch("sys.argv", ["steam-shortcut.py"]), self.assertRaises(SystemExit):
                    shortcut.main()
                self.assertFalse(list(userdata.glob("*/config/shortcuts.vdf")))
                with patch("sys.argv", ["steam-shortcut.py", "--account", "456"]):
                    shortcut.main()
                self.assertTrue((userdata / "456/config/shortcuts.vdf").exists())
                self.assertFalse((userdata / "123/config/shortcuts.vdf").exists())
                offer_start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
