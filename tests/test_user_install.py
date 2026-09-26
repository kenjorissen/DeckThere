import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = (
    "deckthere.sh",
    "deckthere-gui.sh",
    "deckthere-launch.sh",
    "doctor.sh",
    "uninstall.sh",
    "steam-shortcut.py",
    "deckthere_session.py",
    "deckthere_idle.py",
    "deckthere_preferences.py",
    "DeckThereSettings.qml",
    "deckthere_settings.qml",
    "deckthere_qt.py",
    "deckthere_ui.py",
    "deckthere_ui.qml",
    "deckthere_keyboard.py",
    "deckthere_layouts.json",
    "deckthere_ipc.py",
    "deckthere_dashboard.py",
    "deckthere-gui-deps.py",
)


def source_path(name):
    """Checkout organization differs from the deliberately flat installed tree."""
    if name in ("doctor.sh", "uninstall.sh"):
        return Path(name)
    if name in ("steam-shortcut.py", "deckthere-gui-deps.py"):
        return Path("tools") / name
    return Path("src") / name


class UserInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "source checkout"
        self.checkout.mkdir()
        self.home = self.root / "user home"
        self.home.mkdir()
        self.installed = self.home / ".local/share/deckthere"
        self.env = dict(
            os.environ,
            HOME=str(self.home),
            USER_ROOT=str(self.installed),
            XDG_CURRENT_DESKTOP="KDE",
        )
        for name in TOOLS:
            relative = source_path(name)
            destination = self.checkout / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        shutil.copytree(ROOT / "packaging/konsole", self.checkout / "packaging/konsole")

    def install(self):
        source = (ROOT / "setup.sh").read_text()
        block = source.split("# BEGIN USER_INSTALL\n", 1)[1].split("# END USER_INSTALL", 1)[0]
        result = subprocess.run(
            ["bash", "-ec", block],
            cwd=self.checkout,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_copied_tools_survive_checkout_removal_and_helper_preflight_runs(self):
        self.install()
        for name in TOOLS:
            self.assertEqual(
                (self.installed / name).read_bytes(),
                (self.checkout / source_path(name)).read_bytes(),
            )
        self.assertTrue(os.access(self.installed / "deckthere.sh", os.X_OK))
        self.assertTrue(os.access(self.installed / "uninstall.sh", os.X_OK))
        shutil.rmtree(self.checkout)
        (self.home / ".local/share/Steam/userdata/123").mkdir(parents=True)
        result = subprocess.run(
            ["python3", str(self.installed / "steam-shortcut.py"), "--check"],
            cwd=self.root,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Steam account ready: 123", result.stdout)

    def test_installed_layout_and_protocol_imports_without_checkout(self):
        self.install()
        shutil.rmtree(self.checkout)
        result = subprocess.run(
            [
                "python3",
                "-I",
                "-c",
                "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                "import deckthere_keyboard, deckthere_ipc, deckthere_dashboard, deckthere_idle; "
                "assert 'zh-tw-zhuyin' in deckthere_keyboard.CATALOG; "
                "assert set(deckthere_ipc.LAYOUTS) == set(deckthere_keyboard.CATALOG); "
                "assert all(Path(module.__file__).parent == Path(sys.argv[1]) "
                "for module in (deckthere_keyboard, deckthere_ipc, deckthere_dashboard, deckthere_idle))",
                str(self.installed),
            ],
            cwd=self.root,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_launcher_works_without_checkout(self):
        mocks = self.root / "mock commands"
        mocks.mkdir()
        helper = mocks / "helper"
        helper.write_text('#!/bin/bash\nprintf "%s\\n" "$1" >> "$CALLS"\n')
        helper.chmod(0o755)
        sudo = mocks / "sudo"
        sudo.write_text('#!/bin/bash\nshift\nexec "$@"\n')
        sudo.chmod(0o755)
        systemctl = mocks / "systemctl"
        systemctl.write_text("#!/bin/bash\nexit 1\n")  # Mock an already stopped service.
        systemctl.chmod(0o755)
        launcher = self.checkout / "src/deckthere.sh"
        launcher.write_text(
            launcher.read_text()
            .replace("/home/.deckthere/bin/deckthere-root", shlex.quote(str(helper)))
            .replace("/usr/bin/systemctl", shlex.quote(str(systemctl)))
        )
        self.install()
        shutil.rmtree(self.checkout)
        calls = self.root / "calls"
        env = dict(self.env, PATH=str(mocks) + ":" + os.environ["PATH"], CALLS=str(calls))
        result = subprocess.run(
            [str(self.installed / "deckthere.sh")],
            cwd=self.root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["start", "stop"])

    def test_installed_keyboard_supervisor_works_without_checkout(self):
        self.install()
        shutil.rmtree(self.checkout)
        mocks = self.root / "keyboard mocks"
        mocks.mkdir()
        calls = self.root / "calls"
        helper = mocks / "helper"
        helper.write_text('#!/bin/bash\nprintf "%s\\n" "$1" >>"$CALLS"\n')
        helper.chmod(0o755)
        sudo = mocks / "sudo"
        sudo.write_text('#!/bin/bash\n[[ $1 == -n ]] || exit 99\nshift\nexec "$@"\n')
        sudo.chmod(0o755)
        session = self.installed / "deckthere_session.py"
        session.write_text(
            session.read_text().replace("/home/.deckthere/bin/deckthere-root", str(helper))
        )
        # This fixture substitutes only the GUI process; launcher/supervisor are real.
        (self.installed / "deckthere_qt.py").write_text(
            'import sys\nassert sys.argv[1:] in (["--check-runtime"], ["--session"])\n'
        )
        result = subprocess.run(
            [str(self.installed / "deckthere-launch.sh"), "--keyboard"],
            cwd=self.root,
            env=dict(
                self.env,
                PATH=str(mocks) + ":" + os.environ["PATH"],
                WAYLAND_DISPLAY="mock",
                CALLS=str(calls),
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = calls.read_text().splitlines()
        self.assertEqual(actions[0], "start-keyboard")
        self.assertEqual(actions[-1], "stop")
        self.assertTrue(set(actions) <= {"start-keyboard", "keepalive", "stop"})

    def test_installed_uninstaller_works_without_checkout_and_removes_itself(self):
        mocks = self.root / "mocks"
        mocks.mkdir()
        for name, content in (
            ("sudo", "#!/bin/bash\nexit 0\n"),
            ("systemctl", "#!/bin/bash\necho loaded\n"),
        ):
            command = mocks / name
            command.write_text(content)
            command.chmod(0o755)
        uninstaller = self.checkout / "uninstall.sh"
        uninstaller.write_text(
            uninstaller.read_text().replace(
                "export PATH=/usr/sbin:/usr/bin:/sbin:/bin", "# use test mocks"
            )
        )
        self.install()
        shutil.rmtree(self.checkout)
        result = subprocess.run(
            [str(self.installed / "uninstall.sh")],
            cwd=self.root,
            env=dict(self.env, PATH=str(mocks) + ":" + os.environ["PATH"]),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Uninstalled.", result.stdout)
        self.assertEqual(list(self.installed.iterdir()), [self.installed / "launch-mode"])
        self.assertEqual((self.installed / "launch-mode").read_text().strip(), "gui")

    def test_gui_assets_hide_both_toolbars_and_reinstall_discards_gui_state(self):
        self.install()
        for filename, toolbar in (
            ("konsoleui.rc", "mainToolBar"),
            ("sessionui.rc", "sessionToolbar"),
        ):
            path = self.installed / "konsole/data/kxmlgui5/konsole" / filename
            root = ET.parse(path).getroot()
            self.assertEqual(root.attrib["version"], "0")
            element = root.find("ToolBar")
            self.assertEqual(element.attrib["name"], toolbar)
            self.assertEqual(element.attrib["hidden"], "true")
        state = self.installed / "konsole/state/konsolestaterc"
        state.parent.mkdir()
        state.write_text("outdated GUI layout")
        self.install()
        self.assertFalse(state.exists())

    def test_gui_isolates_konsole_and_restores_child_environment_without_checkout(self):
        self.install()
        shutil.rmtree(self.checkout)
        mocks = self.root / "mock gui"
        mocks.mkdir()
        konsole = mocks / "konsole"
        konsole.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            "from pathlib import Path\n"
            'Path(os.environ["GUI_CAPTURE"]).write_text(json.dumps({"args": sys.argv[1:], "env": dict(os.environ)}))\n'
            'args = sys.argv[sys.argv.index("-e") + 1:]\n'
            "os.execv(args[0], args)\n"
        )
        konsole.chmod(0o755)
        (self.installed / "deckthere.sh").write_text(
            "#!/usr/bin/env python3\nimport json, os\nfrom pathlib import Path\n"
            'Path(os.environ["CHILD_CAPTURE"]).write_text(json.dumps(dict(os.environ)))\n'
        )
        keys = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
        for configured in (False, True):
            env = dict(
                self.env,
                PATH=str(mocks) + ":" + os.environ["PATH"],
                GUI_CAPTURE=str(self.root / "gui.json"),
                CHILD_CAPTURE=str(self.root / "child.json"),
            )
            for key in keys:
                env.pop(key, None)
            if configured:
                env.update({key: str(self.root / ("normal " + key)) for key in keys})
                env["XDG_CACHE_HOME"] = ""  # Preserve empty as well as unset.
            result = subprocess.run(
                [str(self.installed / "deckthere-gui.sh")],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            gui = json.loads((self.root / "gui.json").read_text())
            child = json.loads((self.root / "child.json").read_text())
            for key, subdir in zip(keys, ("config", "data", "state", "cache")):
                self.assertEqual(gui["env"][key], str(self.installed / "konsole" / subdir))
                self.assertEqual(child.get(key), env.get(key))
                self.assertEqual(key in child, key in env)
            self.assertIn("--separate", gui["args"])
            self.assertIn("--fullscreen", gui["args"])
            self.assertNotIn("--hide-toolbars", gui["args"])

    def test_reinstall_updates_user_tools_without_removing_other_files(self):
        self.install()
        extra = self.installed / "personal-note.txt"
        extra.write_text("keep")
        (self.installed / "deckthere.sh").write_text("outdated launcher")
        self.install()
        self.assertEqual(
            (self.installed / "deckthere.sh").read_bytes(),
            (self.checkout / "src/deckthere.sh").read_bytes(),
        )
        self.assertEqual(extra.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
