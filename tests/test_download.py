import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "setup.sh").read_text()
PAYLOAD = b"Never execute this test fixture.\n"
SHA1 = hashlib.sha1(PAYLOAD).hexdigest()
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
MANIFEST = f"{SHA1}  vhusbdx86_64\n".encode()


def block(name):
    return SOURCE.split(f"# BEGIN {name}\n", 1)[1].split(f"# END {name}", 1)[0]


class DownloadTests(unittest.TestCase):
    def run_download(
        self, manifest=MANIFEST, payload=PAYLOAD, manual=False, sha256=None, fail="", installed=None
    ):
        with tempfile.TemporaryDirectory(prefix="deckthere download ") as directory:
            root = Path(directory)
            incoming = root / "incoming"
            incoming.mkdir()
            binary = incoming / "vhusbdx86_64"
            binary.write_bytes(payload)
            binary.chmod(0o600)
            (incoming / "SHA1SUM").write_bytes(manifest)
            existing = root / "installed server"
            if installed is not None:
                existing.write_bytes(installed)
            work = root / "work"
            work.mkdir()
            mocks = root / "mocks"
            mocks.mkdir()
            curl = mocks / "curl"
            curl.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\nfrom pathlib import Path\n"
                "args = sys.argv[1:]\n"
                'with open(os.environ["CALLS"], "a") as f: f.write(json.dumps(args) + "\\n")\n'
                'name = args[-1].rsplit("/", 1)[-1]\n'
                'if name == os.environ["FAIL_DOWNLOAD"]: sys.exit(22)\n'
                'Path(args[args.index("--output") + 1]).write_bytes((Path(os.environ["INCOMING"]) / name).read_bytes())\n'
            )
            curl.chmod(0o755)
            env = dict(
                os.environ,
                PATH=f"{mocks}:" + os.environ["PATH"],
                tmp=str(work),
                server_path=str(binary) if manual else "",
                INCOMING=str(incoming),
                CALLS=str(root / "calls"),
                FAIL_DOWNLOAD=fail,
            )
            env.pop("DECKTHERE_SHA256", None)
            if sha256 is not None:
                env["DECKTHERE_SHA256"] = sha256
            script = block("VIRTUALHERE_SOURCES") + block("SERVER_DOWNLOAD")
            script = script.replace(
                "installed_server=/home/.deckthere/bin/vhusbdx86_64",
                f"installed_server={str(existing)!r}",
            )
            script += '\nprintf "PASSED:%s:%s\\n" "$verification_source" "$expected_sha1"\n'
            result = subprocess.run(
                ["bash", "-euc", script],
                env=env,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=5,
            )
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            self.assertEqual(binary.stat().st_mode & 0o777, 0o600)
            if installed is not None:
                self.assertEqual(existing.read_bytes(), installed, "Existing server was modified")
            return result, calls

    def test_default_downloads_both_files_over_https_and_verifies_vendor_checksum(self):
        result, calls = self.run_download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"PASSED:upstream-sha1:{SHA1}", result.stdout)
        self.assertEqual(len(calls.splitlines()), 2)
        self.assertIn("/SHA1SUM", calls)
        self.assertIn("/vhusbdx86_64", calls)
        self.assertEqual(calls.count('"--proto", "=https"'), 2)
        self.assertEqual(calls.count('"--proto-redir", "=https"'), 2)

    def test_matching_installed_server_fetches_only_live_manifest(self):
        result, calls = self.run_download(installed=PAYLOAD, fail="vhusbdx86_64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reusing it without downloading the binary", result.stdout)
        self.assertIn(f"PASSED:upstream-sha1:{SHA1}", result.stdout)
        self.assertEqual(len(calls.splitlines()), 1)
        self.assertIn("/SHA1SUM", calls)
        self.assertNotIn("/vhusbdx86_64", calls)

    def test_outdated_installed_server_downloads_and_verifies_replacement(self):
        for installed in (b"old server", b""):
            with self.subTest(installed=installed):
                result, calls = self.run_download(installed=installed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls.splitlines()), 2)
                self.assertIn(f"PASSED:upstream-sha1:{SHA1}", result.stdout)

    def test_existing_file_never_bypasses_live_manifest_failure(self):
        for options in ({"fail": "SHA1SUM"}, {"manifest": b"invalid"}):
            with self.subTest(options=options):
                result, calls = self.run_download(installed=PAYLOAD, **options)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls.splitlines()), 1)
                self.assertNotIn("PASSED:", result.stdout)

    def test_failed_update_does_not_fall_back_to_outdated_existing_file(self):
        result, calls = self.run_download(installed=b"old server", fail="vhusbdx86_64")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls.splitlines()), 2)
        self.assertNotIn("PASSED:", result.stdout)

    def test_reused_file_still_requires_optional_sha256(self):
        for checksum in (SHA256, "0" * 64):
            with self.subTest(checksum=checksum):
                result, calls = self.run_download(installed=PAYLOAD, sha256=checksum)
                self.assertEqual(result.returncode == 0, checksum == SHA256, result.stderr)
                self.assertEqual(len(calls.splitlines()), 1)

    def test_accepts_uppercase_binary_marker_and_crlf(self):
        result, _ = self.run_download(manifest=f"{SHA1.upper()} *vhusbdx86_64\r\n".encode())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_ambiguous_malformed_and_oversized_manifests(self):
        for manifest in (
            b"",
            b"<html>error</html>",
            MANIFEST * 2,
            f"{SHA1}  vhusbdarm\n".encode(),
            f"{SHA1}  ./vhusbdx86_64\n".encode(),
            f"{SHA1}  ../../vhusbdx86_64\n".encode(),
            b"$(touch SHOULD_NOT_EXECUTE)  vhusbdx86_64\n",
            MANIFEST + b"\xff",
            b" " * 65537,
        ):
            with self.subTest(manifest=manifest[:100]):
                result, _ = self.run_download(manifest=manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid VirtualHere SHA1SUM", result.stderr)
                self.assertNotIn("PASSED:", result.stdout)

    def test_mismatch_cannot_be_bypassed_with_matching_sha256(self):
        for extra in (None, SHA256):
            result, _ = self.run_download(manifest=b"0" * 40 + b"  vhusbdx86_64\n", sha256=extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum mismatch", result.stderr)
            self.assertNotIn("PASSED:", result.stdout)

    def test_download_failure_never_falls_back_to_unverified_installation(self):
        for filename in ("SHA1SUM", "vhusbdx86_64"):
            result, _ = self.run_download(fail=filename)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("PASSED:", result.stdout)

    def test_empty_binary_is_rejected(self):
        result, _ = self.run_download(payload=b"")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Empty server file", result.stderr)

    def test_manual_mode_needs_no_manifest_makes_no_requests_and_warns(self):
        result, calls = self.run_download(manual=True, manifest=b"not a checksum file")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "")
        self.assertIn("without automatic checksum verification", result.stderr)
        self.assertIn("PASSED:manual-unverified:not-verified", result.stdout)
        self.assertNotIn("Verified VirtualHere", result.stdout)

    def test_optional_sha256_is_an_additional_check(self):
        for manual in (False, True):
            for checksum in (SHA256, SHA256.upper(), "0" * 64, "$(exit 0)"):
                with self.subTest(manual=manual, checksum=checksum):
                    result, _ = self.run_download(manual=manual, sha256=checksum)
                    if checksum.lower() == SHA256:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        expected = "user-sha256" if manual else "upstream-sha1+user-sha256"
                        self.assertIn(f"PASSED:{expected}:", result.stdout)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotIn("PASSED:", result.stdout)

    def test_manual_option_guidance_and_explicit_path(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, HOME=directory)
            env.pop("DECKTHERE_SERVER_PATH", None)
            script = block("VIRTUALHERE_SOURCES") + block("DOWNLOAD_OPTIONS")
            result = subprocess.run(
                ["bash", "-euc", script, "setup", "--manual-download"],
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(Path(directory) / "Downloads/vhusbdx86_64"), result.stderr)
            self.assertIn("No download will be made", result.stderr)
            self.assertIn("0600", result.stderr)
            self.assertIn("Verify the executable", result.stderr)
            binary = Path(directory) / "my server"
            binary.write_bytes(PAYLOAD)
            env["DECKTHERE_SERVER_PATH"] = str(binary)
            result = subprocess.run(
                ["bash", "-euc", script], env=env, capture_output=True, text=True, timeout=3
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("does not automatically verify", result.stderr)

    def test_all_automatic_checks_precede_stop_and_privileged_install(self):
        verified = SOURCE.index("# END SERVER_DOWNLOAD")
        self.assertLess(verified, SOURCE.index("sudo systemctl stop deckthere.service"))
        self.assertLess(verified, SOURCE.index("sudo bash <<'DECKTHERE_DATA_SETUP'"))
        self.assertLess(
            verified, SOURCE.index('sudo install -o root -g root -m 755 "$tmp/vhusbdx86_64"')
        )
        self.assertNotIn("PINNED_SHA256", SOURCE)


if __name__ == "__main__":
    unittest.main()
