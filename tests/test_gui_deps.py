import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deps", ROOT / "tools/deckthere-gui-deps.py")
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(
            deps.OPENER, "open", side_effect=AssertionError("Unexpected network")
        )
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_tags_are_strict_and_glibc_is_checked(self):
        self.assertTrue(deps.compatible("cp310", (3, 13)))
        for tag in ("py2", "cp3x", "cp314", "junk"):
            self.assertFalse(deps.compatible(tag, (3, 13)))
        self.assertTrue(deps.platform_compatible("manylinux_2_34_x86_64", (2, 39)))
        self.assertFalse(deps.platform_compatible("manylinux_2_39_x86_64", (2, 34)))
        self.assertFalse(deps.platform_compatible("manylinux_2_34_aarch64", (2, 39)))

    def test_pinned_metadata_and_both_packages_share_one_version(self):
        entry = {"filename": f"shiboken6-{deps.VERSION}-cp310-abi3-manylinux_2_34_x86_64.whl"}
        data = {"info": {"version": deps.VERSION}, "urls": [entry]}
        with patch.object(
            deps.OPENER, "open", return_value=io.BytesIO(json.dumps(data).encode())
        ) as fetch:
            self.assertEqual(deps.select_wheel("shiboken6", (3, 13), (2, 39)), entry)
            self.assertIn(f"/{deps.VERSION}/json", fetch.call_args.args[0])

    def test_bad_checksum_and_download_path_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = {
                "filename": "runtime.whl",
                "url": "https://files.pythonhosted.org/runtime.whl",
                "digests": {"sha256": hashlib.sha256(b"good").hexdigest()},
            }
            with (
                patch.object(deps.OPENER, "open", return_value=io.BytesIO(b"bad")),
                self.assertRaisesRegex(ValueError, "Checksum"),
            ):
                deps.download(entry, Path(directory))
            entry["filename"] = "../outside.whl"
            with self.assertRaises(ValueError):
                deps.download(entry, Path(directory))

    def test_unsafe_zip_paths_and_symlinks_are_rejected(self):
        for name in ("../outside", "/absolute", "bad\\path", "link"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                wheel = Path(directory) / "wheel.zip"
                with zipfile.ZipFile(wheel, "w") as archive:
                    info = zipfile.ZipInfo(name)
                    if name == "link":
                        info.external_attr = 0o120777 << 16
                    archive.writestr(info, "data")
                with self.assertRaises(ValueError):
                    deps.extract(wheel, Path(directory) / "runtime")

    def test_failed_second_package_does_not_touch_existing_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "pylib"
            destination.mkdir()
            (destination / "old").write_text("keep")
            entry = {"filename": "runtime.whl", "digests": {"sha256": "0" * 64}}
            with (
                patch.object(deps.platform, "machine", return_value="x86_64"),
                patch.object(deps.platform, "libc_ver", return_value=("glibc", "2.39")),
                patch.object(deps, "select_wheel", side_effect=[entry, ValueError("missing")]),
                patch.object(deps, "download", return_value=Path(directory) / "mock.whl"),
                patch.object(deps, "extract"),
                self.assertRaises(ValueError),
            ):
                deps.install(destination)
            self.assertEqual((destination / "old").read_text(), "keep")
            self.assertEqual(sorted(item.name for item in Path(directory).iterdir()), ["pylib"])
