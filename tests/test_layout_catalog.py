"""Catalog, mapping provenance, and saved-layout tests; no network or hardware."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_backend import Harness  # noqa: E402

import deckthere_backend  # noqa: E402
import deckthere_keyboard as keyboard  # noqa: E402

spec = importlib.util.spec_from_file_location("layout_builder", ROOT / "tools/build-layouts.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def key(layout, code):
    return next(key for row in keyboard.layout_grid(layout) for key in row if key["code"] == code)


class CatalogTests(unittest.TestCase):
    def test_familiar_variants_and_both_chinese_scripts_are_explicit(self):
        required = {
            "us",
            "us-intl",
            "us-dvorak",
            "uk",
            "uk-ext",
            "fr",
            "fr-std",
            "fr-bepo",
            "be",
            "be-comma",
            "de",
            "ch-de",
            "ch-fr",
            "es",
            "latam",
            "pt",
            "br",
            "it",
            "se",
            "fi",
            "no",
            "dk",
            "pl",
            "pl-214",
            "cz",
            "cz-qwerty",
            "hu",
            "tr",
            "tr-f",
            "ru",
            "ua",
            "ua-enhanced",
            "gr",
            "gr-polytonic",
            "ar",
            "ar-102",
            "ar-azerty",
            "jp",
            "jp-kana",
            "ko",
            "ko-104",
            "zh-pinyin",
            "zh-shuangpin",
            "zh-wubi86",
            "zh-wubi98",
            "zh-wubi-new",
            "zh-tw-zhuyin",
            "zh-tw-pinyin",
            "zh-cangjie",
            "zh-quick",
            "zh-jyutping",
        }
        self.assertTrue(required <= keyboard.CATALOG.keys())
        self.assertEqual(len(keyboard.CATALOG), 56)
        for identity, entry in keyboard.CATALOG.items():
            with self.subTest(layout=identity):
                self.assertRegex(identity, r"^[a-z0-9-]{1,32}$")
                self.assertRegex(entry["sha256"], r"^[a-f0-9]{64}$")
                self.assertTrue(entry["source"].startswith("https://kbdlayout.info/"))
                self.assertIn(entry["kind"], ("layout", "ime"))
                self.assertTrue(entry["note"])
                codes = [key["code"] for row in keyboard.layout_rows(identity) for key in row]
                self.assertEqual(len(codes), len(set(codes)))
                for record in entry["keys"].values():
                    self.assertEqual(len(record["labels"]), 8)
                    self.assertTrue(
                        all(not label or label.isprintable() for label in record["labels"])
                    )
                    self.assertTrue(0 <= record["dead"] <= 255)

    def test_equivalent_names_share_one_mapping_not_copies(self):
        for identity in (
            "zh-pinyin",
            "zh-shuangpin",
            "zh-wubi86",
            "zh-wubi98",
            "zh-tw-pinyin",
            "zh-cangjie",
            "zh-quick",
            "zh-tw-zhuyin",
            "ko",
        ):
            self.assertIs(keyboard.CATALOG[identity]["keys"], keyboard.CATALOG["us"]["keys"])
        self.assertIs(keyboard.CATALOG["fi"]["keys"], keyboard.CATALOG["se"]["keys"])
        self.assertNotEqual(
            keyboard.CATALOG["ch-de"]["mapping"], keyboard.CATALOG["ch-fr"]["mapping"]
        )

    def test_representative_regional_positions_are_not_us_fallbacks(self):
        for layout, code, normal in (
            ("fr", 4, "q"),
            ("be", 4, "q"),
            ("de", 28, "z"),
            ("es", 51, "ñ"),
            ("pt", 51, "ç"),
            ("br", 51, "ç"),
            ("br", 135, "/"),
            ("se", 47, "å"),
            ("fi", 47, "å"),
            ("no", 47, "å"),
            ("dk", 47, "å"),
            ("tr", 12, "ı"),
            ("ru", 20, "й"),
            ("ua", 20, "й"),
            ("ch-de", 51, "ö"),
            ("ch-fr", 51, "é"),
        ):
            with self.subTest(layout=layout, code=code):
                self.assertEqual(key(layout, code)["normal"], normal)
        self.assertEqual(key("pl", 4)["altgr"], "ą")

    def test_caps_shift_altgr_and_dead_key_previews_use_explicit_states(self):
        state = keyboard.TouchKeys()
        state.caps = True
        self.assertEqual(state.label(key("us", 4)), "A")
        self.assertEqual(state.label(key("us", 30)), "1")
        self.assertEqual(state.label(key("de", 30)), "!")
        state.held.add(225)
        self.assertEqual(state.label(key("us", 4)), "a")
        self.assertEqual(state.label(key("de", 30)), "1")
        state.caps = False
        state.held.add(230)
        record = key("pl", 4)
        self.assertEqual(state.label(record), "Ą")
        state = keyboard.TouchKeys()
        self.assertIn("◌", state.label(key("de", 53)))

    def test_ime_overlays_are_secondary_legends_not_unicode_reports(self):
        state = keyboard.TouchKeys()
        self.assertEqual(state.label(key("zh-tw-zhuyin", 20)), "q\nㄆ")
        self.assertEqual(state.label(key("zh-cangjie", 20)), "q\n手")
        self.assertEqual(state.label(key("ko", 20)), "q\nㅂ")
        state.held.add(225)
        self.assertEqual(state.label(key("ko", 20)), "Q\nㅃ")
        report = keyboard.KeyState().update(key("zh-cangjie", 20)["code"], True)
        self.assertEqual(report, bytes([0, 0, 20, 0, 0, 0, 0, 0]))

    def test_jis_abnt_and_korean_have_reachable_distinct_usages(self):
        for layout, wanted in (
            ("br", {135}),
            ("jp", {135, 136, 137, 138, 139}),
            ("ko", {144, 145}),
        ):
            codes = {item["code"] for row in keyboard.layout_rows(layout) for item in row}
            self.assertTrue(wanted <= codes)
            for code in wanted:
                self.assertEqual(keyboard.KeyState().update(code, True)[2], code)


class LayoutPreferenceTests(unittest.TestCase):
    def test_bad_preferences_fall_back_without_following_links_or_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout"
            for data in (b"", b"not-a-layout", b"US", b"de" + b" " * 10000, b"\xff"):
                path.write_bytes(data)
                self.assertEqual(deckthere_backend.read_layout(path), "us")
            path.unlink()
            self.assertEqual(deckthere_backend.read_layout(path), "us")
            other = Path(directory) / "other"
            other.write_text("de\n")
            path.symlink_to(other)
            self.assertEqual(deckthere_backend.read_layout(path), "us")
            path.unlink()
            os.mkfifo(path)
            self.assertEqual(deckthere_backend.read_layout(path), "us")
            path.unlink()
            path.mkdir()
            self.assertEqual(deckthere_backend.read_layout(path), "us")

    def test_save_is_private_atomic_and_not_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout"
            deckthere_backend.save_layout(path, "zh-tw-zhuyin")
            self.assertEqual(deckthere_backend.read_layout(path), "zh-tw-zhuyin")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch.object(deckthere_backend.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    deckthere_backend.save_layout(path, "de")
            self.assertEqual(deckthere_backend.read_layout(path), "zh-tw-zhuyin")
            self.assertEqual(list(Path(directory).iterdir()), [path])
            with self.assertRaises(ValueError):
                deckthere_backend.save_layout(path, "$(touch bad)")

    def test_backend_remembers_choice_in_a_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout"
            with Harness(layout_store=path) as harness:
                client = harness.connect()
                client.wait_for("status")
                client.send({"op": "layout", "layout": "zh-cangjie"})
                self.assertEqual(client.wait_for("status")["layout"], "zh-cangjie")
                client.close()
            with Harness(layout_store=path) as harness:
                client = harness.connect()
                self.assertEqual(client.wait_for("status")["layout"], "zh-cangjie")
                client.close()


class BuilderTests(unittest.TestCase):
    def fixture(self):
        root = ET.Element("KeyboardLayout")
        physical = ET.SubElement(root, "PhysicalKeys")
        for scan in builder.SCANCODES:
            pk = ET.SubElement(physical, "PK", SC=f"{scan:02X}")
            ET.SubElement(pk, "Result", Text="a")
            ET.SubElement(pk, "Result", Text="A", With="VK_SHIFT")
            if scan == 2:
                ET.SubElement(pk, "Result", Text="!", With="VK_CAPITAL")
                ET.SubElement(pk, "Result", Text="1", With="VK_SHIFT VK_CAPITAL")
        return root

    def test_caps_fallback_and_explicit_caps_are_distinct(self):
        data = ET.tostring(self.fixture())
        keys = builder.parse_keys(data, "ansi")
        self.assertEqual(keys["30"]["labels"][4:6], ["!", "1"])
        self.assertEqual(keys["31"]["labels"][4:6], ["a", "A"])
        self.assertEqual(keys["31"]["labels"][2:4], ["", ""])

    def test_dead_key_and_control_character_parsing(self):
        result = ET.fromstring('<Result><DeadKeyTable Accent="´" /></Result>')
        self.assertEqual(builder.value(result), ("´", True))
        self.assertEqual(
            builder.value(ET.fromstring('<Result TextCodepoints="0000" />')), ("", False)
        )
        with self.assertRaises(ValueError):
            builder.parse_keys(b"<KeyboardLayout><PhysicalKeys /></KeyboardLayout>", "ansi")

    def test_catalog_writer_roundtrips_escaped_labels(self):
        data = json.loads((ROOT / "src/deckthere_layouts.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.json"
            builder.write_catalog(data, path)
            self.assertEqual(json.loads(path.read_text()), data)
