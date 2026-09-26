import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PreflightTests(unittest.TestCase):
    def block(self, folder):
        source = (ROOT / "setup.sh").read_text()
        block = source.split("sudo bash <<'DECKTHERE_PREFLIGHT'\n", 1)[1].split(
            "\nDECKTHERE_PREFLIGHT", 1
        )[0]
        for path in (
            "/home/.deckthere/bin",
            "/home/.deckthere/data",
            "/etc/systemd/system",
            "/etc/sudoers.d",
        ):
            block = block.replace(path, str(folder / path.lstrip("/")))
        return block

    def test_probes_leave_no_files_or_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            result = subprocess.run(
                ["bash", "-c", self.block(folder)], capture_output=True, text=True, timeout=3
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(folder.iterdir()), [])

    def test_unwritable_path_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            fake = folder / "mktemp"
            fake.write_text("#!/bin/bash\nexit 1\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=str(folder) + ":" + os.environ["PATH"])
            result = subprocess.run(
                ["bash", "-c", self.block(folder)],
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cannot write installation path", result.stderr)

    def test_preflight_precedes_download(self):
        source = (ROOT / "setup.sh").read_text()
        download = source.index("curl --fail")
        for check in (
            "sudo -v",
            "DECKTHERE_PREFLIGHT",
            "steam-shortcut.py --check",
            "sha256sum konsole",
        ):
            self.assertLess(source.index(check), download)

    def test_passwordless_probe_ignores_cached_authentication(self):
        source = (ROOT / "setup.sh").read_text()
        self.assertIn("/etc/sudoers.d/zz-deckthere", source)
        self.assertIn("sudo -k -n /home/.deckthere/bin/deckthere-root check", source)
        self.assertIn(
            "sudo -k -n /home/.deckthere/bin/deckthere-root check", (ROOT / "doctor.sh").read_text()
        )

    def test_helper_check_is_harmless(self):
        source = (
            (ROOT / "src/deckthere-root")
            .read_text()
            .replace("[[ $EUID == 0 && $# == 1 ]]", "[[ $# == 1 ]]")
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", "-c", source, "deckthere-root", "check"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_metadata_without_git_checkout(self):
        source = (ROOT / "setup.sh").read_text()
        block = (
            "commit=unknown" + source.split("commit=unknown", 1)[1].split("\nprintf '%s ALL=", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "vhusbdx86_64").write_bytes(b"test binary fixture")
            env = dict(
                os.environ,
                tmp=str(folder),
                expected_sha1="not-verified",
                verification_source="manual-unverified",
            )
            result = subprocess.run(
                ["bash", "-c", block],
                cwd=folder,
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            info = (folder / "build-info.txt").read_text()
            self.assertIn("DECKTHERE_COMMIT=unknown\n", info)
            self.assertIn(
                "VIRTUALHERE_SHA256=" + hashlib.sha256(b"test binary fixture").hexdigest(), info
            )
            self.assertIn("INSTALLED_UTC=", info)
            self.assertIn("VIRTUALHERE_SHA1=not-verified\n", info)
            self.assertIn("VIRTUALHERE_VERIFICATION=manual-unverified\n", info)
            self.assertNotIn(str(folder), info)


if __name__ == "__main__":
    unittest.main()
