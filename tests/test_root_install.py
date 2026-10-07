"""Install the real backend files into a temporary tree, never system paths."""

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "deckthere-root",
    "deckthere_repair.py",
    "touch-stop.py",
    "deckthere_backend.py",
    "deckthere_activity.py",
    "deckthere_haptics.py",
    "deckthere_hardware.py",
    "deckthere_preferences.py",
    "deckthere_keyboard.py",
    "deckthere_layouts.json",
    "deckthere_ipc.py",
)


class RootInstallTests(unittest.TestCase):
    def test_backend_imports_its_installed_modules_and_catalog_without_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            checkout = temporary / "source checkout"
            (checkout / "src").mkdir(parents=True)
            installed = temporary / "installed root bin"
            installed.mkdir()
            for name in FILES:
                shutil.copy2(ROOT / "src" / name, checkout / "src" / name)
            setup = (ROOT / "setup.sh").read_text()
            block = setup.split("# BEGIN ROOT_CODE_INSTALL\n", 1)[1].split(
                "# END ROOT_CODE_INSTALL", 1
            )[0]
            # Only replace privilege/ownership and the fixed destination. Exercise
            # the same source filenames, permissions and flattening as setup.
            block = block.replace("sudo install -o root -g root", "install")
            block = block.replace("/home/.deckthere/bin", shlex.quote(str(installed)))
            self.assertNotIn("sudo", block)
            self.assertNotIn("/home/.deckthere", block)
            result = subprocess.run(
                ["bash", "-ec", block],
                cwd=checkout,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in FILES:
                self.assertEqual(
                    (installed / name).read_bytes(), (checkout / "src" / name).read_bytes()
                )
                self.assertEqual(
                    (installed / name).stat().st_mode & 0o777,
                    0o755 if name in ("deckthere-root", "deckthere_repair.py") else 0o644,
                )
            shutil.rmtree(checkout)
            # --help imports the backend and resolves all catalog choices, but
            # exits before constructing hardware or accessing service settings.
            result = subprocess.run(
                ["python3", "-I", str(installed / "deckthere_backend.py"), "--help"],
                cwd=temporary,
                env=dict(os.environ, PYTHONPATH=str(checkout / "src")),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("zh-tw-zhuyin", result.stdout)
