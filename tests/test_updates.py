"""Offline update checks, hostile archives, and transactional installation."""

import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import deckthere_update as update  # noqa: E402
import deckthere_update_common as common  # noqa: E402
import deckthere_update_install as installer  # noqa: E402


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as target:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), 0o644
            if name.endswith(
                (".sh", "deckthere-root", "deckthere_update_install.py", "deckthere_repair.py")
            ):
                entry.mode = 0o755
            target.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def source_archive():
    manifest = json.loads((ROOT / "packaging/update-manifest.json").read_text())
    sources = set(manifest["files"].values()) | {
        "packaging/update-manifest.json",
        "tools/deckthere-gui-deps.py",
    }
    return archive({"DeckThere-test/" + name: (ROOT / name).read_bytes() for name in sources})


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.release = {"version": "v1.5.0", "commit": "a" * 40}
        self.current = {"schema": 1, "channel": "stable", "version": "v1.4.0", "commit": "b" * 40}
        self.save_current()

    def save_current(self):
        (self.base / "update-info.json").write_text(json.dumps(self.current))

    def test_apply_confirms_with_agent_running_before_authorization(self):
        for choice in (0, 1):
            with self.subTest(choice=choice):
                directory = self.base / ".updates" / "release-test"
                directory.mkdir(parents=True, exist_ok=True)
                payload = directory / "update.tar.gz"
                payload.write_bytes(b"test")
                request = self.base / "update-request.json"
                request.write_text(
                    json.dumps({"archive": str(payload), "sha256": "test", "version": "v1.6.0"})
                )
                events = []

                @contextmanager
                def agent():
                    events.append("agent started")
                    yield
                    events.append("agent stopped")

                def dialog(args, **kwargs):
                    if "--yesno" in args:
                        events.append("confirm")
                        self.assertEqual(events, ["agent started", "confirm"])
                        return choice
                    return 0

                def authorize(*args, **kwargs):
                    events.append("authorize")
                    return SimpleNamespace(returncode=0, stderr="")

                with (
                    patch.object(update, "validate_bundle"),
                    patch.object(update.launch_check, "trusted", return_value=True),
                    patch.object(update.launch_check, "authentication_agent", agent),
                    patch.object(update.launch_check, "run", side_effect=dialog),
                    patch.object(update.subprocess, "run", side_effect=authorize),
                ):
                    self.assertEqual(update.apply_pending(self.base), 0 if choice == 0 else 126)
                expected = ["agent started", "confirm"]
                if choice == 0:
                    expected.append("authorize")
                self.assertEqual(events, expected + ["agent stopped"])
                self.assertFalse(request.exists())
                self.assertFalse(directory.exists())

    def test_only_newer_stable_versions_are_offered(self):
        with patch.object(update, "latest", return_value=self.release):
            self.assertTrue(update.check(self.base)["available"])
            self.current["version"] = "v1.5.0"
            self.save_current()
            self.assertFalse(update.check(self.base)["available"])
            self.current["version"] = "v2.0.0"
            self.save_current()
            self.assertFalse(update.check(self.base)["available"])

    def test_development_installations_never_fetch_or_apply(self):
        self.current["channel"] = "development"
        self.save_current()
        with patch.object(update, "latest") as latest:
            self.assertFalse(update.check(self.base)["available"])
            with self.assertRaises(common.UpdateError):
                update.prepare(self.base, "v1.5.0", "a" * 40)
            latest.assert_not_called()

    def test_missing_install_metadata_requires_bootstrap(self):
        (self.base / "update-info.json").unlink()
        with self.assertRaisesRegex(common.UpdateError, "setup"):
            update.installed(self.base)

    def test_resolves_annotated_release_and_rejects_prerelease(self):
        response = {"tag_name": "v1.5.0", "draft": False, "prerelease": False}
        with patch.object(
            update,
            "api",
            side_effect=[
                response,
                {"object": {"type": "tag", "sha": "b" * 40}},
                {"object": {"type": "commit", "sha": "a" * 40}},
            ],
        ) as api:
            self.assertEqual(update.latest()["commit"], "a" * 40)
            self.assertEqual(api.call_args.args[0], "/git/tags/" + "b" * 40)
        response["prerelease"] = True
        with (
            patch.object(update, "api", return_value=response),
            self.assertRaises(common.UpdateError),
        ):
            update.latest()

    def test_no_published_release_is_a_normal_result(self):
        with patch.object(update, "latest", return_value=None):
            self.assertFalse(update.check(self.base)["available"])

    def test_changed_release_cannot_be_applied_from_stale_check(self):
        with (
            patch.object(update, "latest", return_value=self.release),
            patch.object(update, "fetch") as fetch,
        ):
            with self.assertRaises(common.UpdateError):
                update.prepare(self.base, "v1.5.0", "c" * 40)
            fetch.assert_not_called()

    def test_preparation_pins_commit_preserves_preferences_and_writes_request(self):
        (self.base / "haptics").write_text("1 strong\n")
        with (
            patch.object(update, "latest", return_value=self.release),
            patch.object(update, "fetch", return_value=source_archive()) as fetch,
            patch.object(update, "owned_command"),
        ):
            self.assertTrue(update.prepare(self.base, "v1.5.0", "a" * 40)["ready"])
        self.assertIn("/tar.gz/" + "a" * 40, fetch.call_args.args[0])
        request = json.loads((self.base / "update-request.json").read_text())
        metadata, files = common.validate_bundle(
            Path(request["archive"]).read_bytes(), request["sha256"]
        )
        self.assertEqual(metadata["version"], "v1.5.0")
        self.assertEqual((self.base / "haptics").read_text(), "1 strong\n")
        self.assertNotIn("root/vhusbdx86_64", files)
        self.assertNotIn("user/haptics", files)

    def test_failed_prepare_removes_partial_downloads_without_request(self):
        with (
            patch.object(update, "latest", return_value=self.release),
            patch.object(update, "fetch", side_effect=OSError("offline")),
        ):
            with self.assertRaises(OSError):
                update.prepare(self.base, "v1.5.0", "a" * 40)
        self.assertFalse((self.base / "update-request.json").exists())
        self.assertEqual(list((self.base / ".updates").iterdir()), [])

    def test_manifest_covers_installed_code(self):
        manifest = json.loads((ROOT / "packaging/update-manifest.json").read_text())["files"]
        setup = (ROOT / "setup.sh").read_text().replace("\\\n", " ")
        import shlex

        for line in setup.splitlines():
            if line.startswith(("install -m", "sudo install -o root -g root -m")):
                for token in shlex.split(line):
                    if token.startswith(("src/", "tools/")) and (ROOT / token).is_file():
                        self.assertIn(token, manifest.values())

    def test_archive_traversal_symlink_and_checksum_are_rejected(self):
        for name in ("../escape", "/absolute", "root/../escape", "root\\escape"):
            with self.assertRaises(common.UpdateError):
                common.members(archive({name: b"bad"}))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as target:
            entry = tarfile.TarInfo("link")
            entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/shadow"
            target.addfile(entry)
        with self.assertRaises(common.UpdateError):
            common.members(output.getvalue())
        with self.assertRaises(common.UpdateError):
            common.validate_bundle(b"bad", "0" * 64)

    def test_settings_cannot_be_present_in_update_bundle(self):
        metadata = dict(self.current)
        data = archive({"meta.json": json.dumps(metadata).encode(), "user/haptics": b"0 normal"})
        with self.assertRaisesRegex(common.UpdateError, "settings"):
            common.validate_bundle(data, hashlib.sha256(data).hexdigest())

    def test_publication_failure_restores_existing_and_removes_new_files(self):
        existing, new = self.base / "existing.py", self.base / "new.py"
        existing.write_bytes(b"old")
        existing.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            installer.publish(
                {existing: (b"new", 0o755), new: (b"added", 0o644)},
                lambda: (_ for _ in ()).throw(RuntimeError("failed")),
            )
        self.assertEqual(existing.read_bytes(), b"old")
        self.assertEqual(existing.stat().st_mode & 0o777, 0o600)
        self.assertFalse(new.exists())
        self.assertEqual(list(self.base.glob(".update-*")), [])

    def test_user_install_preserves_preferences_and_swaps_runtime(self):
        (self.base / "haptics").write_bytes(b"1 strong")
        (self.base / "pylib").mkdir()
        (self.base / "pylib/old.py").write_bytes(b"old")
        files = {
            "user/deckthere_ui.py": (b"new UI", 0o644),
            "user/pylib/new.py": (b"new Qt", 0o644),
        }
        installer.install_user(self.base, files, self.current)
        self.assertEqual((self.base / "haptics").read_bytes(), b"1 strong")
        self.assertFalse((self.base / "pylib/old.py").exists())
        self.assertEqual((self.base / "pylib/new.py").read_bytes(), b"new Qt")

    def test_symlink_targets_are_not_followed(self):
        original = self.base / "original"
        original.write_bytes(b"keep")
        target = self.base / "target.py"
        target.symlink_to(original)
        with self.assertRaises(common.UpdateError):
            installer.publish({target: (b"bad", 0o644)})
        self.assertEqual(original.read_bytes(), b"keep")

    def test_installer_requires_owner_and_newer_release_before_mutation(self):
        with (
            patch.object(installer.os, "geteuid", return_value=0),
            patch.object(installer.repair, "read_file", return_value=(b"1000", 0o600)),
            patch.dict(os.environ, {"PKEXEC_UID": "1001"}),
            patch.object(installer, "publish") as publish,
        ):
            with self.assertRaisesRegex(common.UpdateError, "owner"):
                installer.apply(Path("unused"), "0" * 64)
            publish.assert_not_called()

    def test_full_installer_rolls_back_system_files_if_user_install_fails(self):
        self.exercise_installer(fail_user=True)

    def test_full_installer_preserves_license_and_installs_both_scopes(self):
        self.exercise_installer(fail_user=False)

    def exercise_installer(self, fail_user):
        root = self.base / "system/bin"
        root.mkdir(parents=True)
        unit = self.base / "system/deckthere.service"
        unit.write_bytes(b"old unit")
        lock = self.base / "run/launch/lock"
        lock.parent.parent.mkdir()
        home = self.base / "home"
        user_base = home / ".local/share/deckthere"
        user_base.mkdir(parents=True)
        (user_base / "haptics").write_bytes(b"1 strong")
        license_file = self.base / "system/data/config.ini"
        license_file.parent.mkdir()
        license_file.write_bytes(b"private license fixture")
        uid = os.getuid()
        (root / "owner-uid").write_text(str(uid))
        (root / "update-info.json").write_text(json.dumps(self.current))
        (root / "build-info.txt").write_text("DECKTHERE_COMMIT=old\nVIRTUALHERE_SHA256=keep\n")
        (root / "deckthere-root").write_bytes(b"old backend")
        (root / "vhusbdx86_64").write_bytes(b"keep server")
        with patch.object(update, "owned_command"):
            data, digest = update.make_bundle(source_archive(), self.release, user_base, self.base)
        payload = self.base / "bundle.tar.gz"
        payload.write_bytes(data)
        owner = SimpleNamespace(pw_uid=uid, pw_gid=os.getgid(), pw_dir=str(home))

        def child(owner, files, metadata, lock):
            if fail_user:
                raise common.UpdateError("simulated user failure")
            installer.install_user(user_base, files, metadata)

        with ExitStack() as stack:
            for guard in (
                patch.object(installer, "BIN", root),
                patch.object(installer.os, "geteuid", return_value=0),
                patch.dict(os.environ, {"PKEXEC_UID": str(uid)}),
                patch.object(installer.pwd, "getpwuid", return_value=owner),
                patch.object(installer.repair, "LOCK", lock),
                patch.object(installer.repair, "UNIT", unit),
                patch.object(installer.repair, "parents"),
                patch.object(installer.repair, "safe_stat"),
                patch.object(
                    installer.repair,
                    "read_file",
                    side_effect=lambda path: (path.read_bytes(), 0o644),
                ),
                patch.object(installer.repair, "service", return_value={"ActiveState": "inactive"}),
                patch.object(
                    installer.repair, "command", return_value=SimpleNamespace(returncode=0)
                ),
                patch.object(installer, "child_install", side_effect=child),
            ):
                stack.enter_context(guard)
            if fail_user:
                with self.assertRaises(common.UpdateError):
                    installer.apply(payload, digest)
                self.assertEqual((root / "deckthere-root").read_bytes(), b"old backend")
                self.assertEqual(unit.read_bytes(), b"old unit")
                self.assertEqual(
                    json.loads((root / "update-info.json").read_text())["version"], "v1.4.0"
                )
            else:
                installer.apply(payload, digest)
                self.assertEqual(
                    (root / "deckthere-root").read_bytes(),
                    (ROOT / "src/deckthere-root").read_bytes(),
                )
                self.assertEqual(
                    json.loads((root / "update-info.json").read_text())["version"], "v1.5.0"
                )
                self.assertEqual(
                    json.loads((user_base / "update-info.json").read_text())["version"], "v1.5.0"
                )
                self.assertIn("VIRTUALHERE_SHA256=keep", (root / "build-info.txt").read_text())
            self.assertEqual((root / "vhusbdx86_64").read_bytes(), b"keep server")
            self.assertEqual(license_file.read_bytes(), b"private license fixture")
            self.assertEqual((user_base / "haptics").read_bytes(), b"1 strong")


if __name__ == "__main__":
    unittest.main()
