import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # Discover imports tests/ as top-level; reach source modules.

import deckthere_hardware  # noqa: E402
import deckthere_keyboard  # noqa: E402

MODIFIERS = {224: 0, 225: 1, 226: 2, 227: 3, 228: 4, 229: 5, 230: 6, 231: 7}


class KeyStateTests(unittest.TestCase):
    def test_reports_are_eight_bytes_with_reserved_byte_zero(self):
        state = deckthere_keyboard.KeyState()
        self.assertEqual(state.report(), bytes(8))
        self.assertEqual(state.update(4, True), bytes([0, 0, 4, 0, 0, 0, 0, 0]))
        self.assertEqual(state.update(4, False), bytes(8))

    def test_modifier_bits_are_independent_of_letter_keys(self):
        state = deckthere_keyboard.KeyState()
        state.update(225, True)
        state.update(4, True)
        self.assertEqual(state.report(), bytes([0b00000010, 0, 4, 0, 0, 0, 0, 0]))
        state.update(224, True)
        self.assertEqual(state.report()[0], 0b00000011)
        state.update(225, False)
        self.assertEqual(state.report()[0], 0b00000001)

    def test_keys_are_sorted_and_deduplicated(self):
        state = deckthere_keyboard.KeyState()
        for code in (29, 4, 20, 29):
            state.update(code, True)
        self.assertEqual(state.report()[2:5], bytes([4, 20, 29]))
        state.update(4, False)
        state.update(4, False)
        self.assertEqual(state.report()[2:5], bytes([20, 29, 0]))

    def test_rollover_guard_ignores_modifiers_and_holds_at_six(self):
        state = deckthere_keyboard.KeyState()
        state.update(225, True)
        for code in range(4, 11):
            if code < 10:
                state.update(code, True)
            else:
                with self.assertRaises(ValueError):
                    state.update(code, True)
        self.assertEqual(sum(byte != 0 for byte in state.report()[2:]), 6)
        state.update(225, False)
        self.assertEqual(state.report()[0], 0)

    def test_clear_releases_everything(self):
        state = deckthere_keyboard.KeyState()
        state.update(225, True)
        state.update(42, True)
        self.assertEqual(state.clear(), bytes(8))

    def test_only_boolean_bools_and_known_usages_are_accepted(self):
        # bool is a subclass of int; a truthy int must not silently count as a press.
        for code, down in (
            (4, 1),
            (4, "yes"),
            (True, True),
            (3, True),
            (71, True),
            (232, True),
            (255, True),
            (4, None),
        ):
            with self.subTest(code=code, down=down):
                with self.assertRaises(ValueError):
                    deckthere_keyboard.KeyState().update(code, down)


class LayoutTests(unittest.TestCase):
    def test_every_layout_has_six_rows_and_usable_labels(self):
        for name in deckthere_keyboard.LAYOUT_NAMES:
            with self.subTest(layout=name):
                rows = deckthere_keyboard.layout_rows(name)
                self.assertEqual(len(rows), 6)
                for row in rows:
                    for key in row:
                        self.assertIn(key["code"], deckthere_keyboard.ALLOWED_KEYS)
                        self.assertGreater(key["weight"], 0)
                        self.assertTrue(key["normal"], key)
                        self.assertEqual(len(key["labels"]), 8)
                        self.assertTrue(all(isinstance(label, str) for label in key["labels"]))
                        self.assertTrue(deckthere_keyboard.TouchKeys().label(key))

    def test_layouts_share_modifiers_but_differ_where_expected(self):
        us, de = (deckthere_keyboard.layout_rows(name) for name in ("us", "de"))
        self.assertEqual(
            [key["code"] for key in us[3]], [225, 29, 27, 6, 25, 5, 17, 16, 54, 55, 56, 229]
        )
        # German needs AltGr plus one extra key in that row (the < > key).
        self.assertEqual(len(de[3]), len(us[3]) + 1)
        self.assertEqual(de[0][0]["code"], 53)  # ^ before 1
        self.assertEqual(de[3][0]["code"], 225)

    def test_altgr_labels_exist_only_for_altgr_layouts(self):
        us = deckthere_keyboard.layout_rows("us")
        self.assertFalse(any(key["altgr"] for row in us for key in row))
        for name in ("uk", "de", "fr"):
            with self.subTest(layout=name):
                labeled = [
                    key
                    for row in deckthere_keyboard.layout_rows(name)
                    for key in row
                    if key["altgr"]
                ]
                self.assertTrue(labeled, name)

    def test_letter_flags_identify_caps_lock_targets(self):
        rows = deckthere_keyboard.layout_rows("de")
        flags = {key["normal"]: key["letter"] for row in rows for key in row}
        self.assertTrue(flags["a"])
        self.assertTrue(flags["ö"])
        self.assertFalse(flags["1"])
        self.assertFalse(flags["→"])

    def test_german_swaps_y_and_z_at_the_same_physical_positions(self):
        # Same HID usages as US; the PC's layout decides the letter produced.
        de = [key for row in deckthere_keyboard.layout_rows("de") for key in row]
        labels = {key["code"]: key["normal"] for key in de}
        self.assertEqual((labels[28], labels[29]), ("z", "y"))

    def test_unknown_layout_is_rejected(self):
        with self.assertRaises(ValueError):
            deckthere_keyboard.layout_rows("not-a-layout")


class GridTests(unittest.TestCase):
    def test_every_row_fills_the_same_width_without_empty_keys(self):
        for name in deckthere_keyboard.LAYOUT_NAMES:
            with self.subTest(layout=name):
                for row in deckthere_keyboard.layout_grid(name):
                    self.assertEqual(sum(key["span"] for key in row), 1000)
                    self.assertTrue(all(key["span"] >= 1 for key in row))

    def test_grid_preserves_key_order_and_labels(self):
        for name in deckthere_keyboard.LAYOUT_NAMES:
            with self.subTest(layout=name):
                rows = deckthere_keyboard.layout_rows(name)
                grid = deckthere_keyboard.layout_grid(name)
                self.assertEqual(len(grid), len(rows))
                for expected, actual in zip(rows, grid):
                    self.assertEqual(
                        [key["code"] for key in expected], [key["code"] for key in actual]
                    )
                    self.assertEqual(
                        [key["normal"] for key in expected], [key["normal"] for key in actual]
                    )

    def test_essential_keys_are_reachable_in_every_layout(self):
        essentials = {
            41: "Esc",
            40: "Enter",
            42: "Backspace",
            43: "Tab",
            44: "Space",
            57: "Caps",
            224: "Ctrl",
            225: "Shift",
            229: "Shift",
            230: "AltGr",
            80: "Left",
            81: "Down",
            82: "Up",
            79: "Right",
        }
        for name in deckthere_keyboard.LAYOUT_NAMES:
            with self.subTest(layout=name):
                codes = {key["code"] for row in deckthere_keyboard.layout_grid(name) for key in row}
                self.assertEqual(essentials.keys() - codes, set())

    def test_space_is_the_widest_key(self):
        for name in deckthere_keyboard.LAYOUT_NAMES:
            with self.subTest(layout=name):
                keys = [key for row in deckthere_keyboard.layout_grid(name) for key in row]
                widest = max(keys, key=lambda key: key["span"])
                self.assertEqual((widest["code"], widest["normal"]), (44, "Space"))

    def test_proportional_widths_are_near_exact_for_equal_keys(self):
        row = deckthere_keyboard.layout_grid("us")[5]  # Esc and the function keys
        spans = {key["span"] for key in row}
        self.assertLessEqual(max(spans) - min(spans), 1)

    def test_cell_hit_testing_covers_every_key_exactly_once(self):
        for name in deckthere_keyboard.LAYOUT_NAMES:
            with self.subTest(layout=name):
                for row in deckthere_keyboard.layout_grid(name):
                    boundaries = []
                    cell = 0
                    for key in row:
                        boundaries.append((cell, cell + key["span"], key))
                        cell += key["span"]
                    self.assertEqual(cell, 1000)
                    for start, end, key in boundaries:
                        self.assertEqual(
                            key["code"], next(k["code"] for s, e, k in boundaries if s <= start < e)
                        )

    def test_largest_remainder_sums_exactly_and_never_returns_zero(self):
        self.assertEqual(deckthere_keyboard.largest_remainder([1, 1, 1], 3), [1, 1, 1])
        self.assertEqual(sum(deckthere_keyboard.largest_remainder([5, 1, 1, 1], 15)), 15)
        self.assertEqual(sum(deckthere_keyboard.largest_remainder([1] * 14, 15)), 15)
        self.assertTrue(
            all(span >= 1 for span in deckthere_keyboard.largest_remainder([1] * 14, 15))
        )
        with self.assertRaises(ValueError):
            deckthere_keyboard.largest_remainder([1, 1, 1], 2)


class BrightnessTests(unittest.TestCase):
    def shell_target(self, maximum, percent, product):
        source = (ROOT / "src/deckthere-root").read_text()
        functions = source.split("# BEGIN BRIGHTNESS_FUNCTIONS\n", 1)[1].split(
            "# END BRIGHTNESS_FUNCTIONS", 1
        )[0]
        script = functions + '\nbrightness_target "$1" "$2"'
        with tempfile.TemporaryDirectory() as directory:
            product_path = Path(directory) / "product"
            product_path.write_text(product + "\n")
            result = subprocess.run(
                ["bash", "-euc", script, "bash", str(maximum), str(percent)],
                env={"PATH": "/usr/bin:/bin", "DMI_PRODUCT_FILE": str(product_path)},
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout)

    def test_python_curve_matches_the_shell_implementation(self):
        for product, maximum in (("Galileo", 599000), ("Jupiter", 599000), ("", 65535)):
            with self.subTest(product=product, maximum=maximum):
                for percent in (0, 1, 10, 33, 50, 90, 95, 100):
                    expected = self.shell_target(maximum, percent, product)
                    actual = deckthere_hardware.brightness_target(maximum, percent, product)
                    self.assertEqual(actual, expected)

    def test_curves_are_bounded_monotonic_and_never_negative(self):
        for product, maximum in (("Galileo", 599000), ("Jupiter", 255), ("other", 1)):
            with self.subTest(product=product):
                values = [
                    deckthere_hardware.brightness_target(maximum, percent, product)
                    for percent in range(101)
                ]
                self.assertEqual(values, sorted(values))
                self.assertTrue(all(0 <= value <= maximum for value in values))


class ReportDescriptorTests(unittest.TestCase):
    def test_descriptor_is_the_standard_eight_byte_boot_keyboard(self):
        descriptor = deckthere_hardware.REPORT_DESCRIPTOR
        self.assertEqual(len(descriptor), 64)
        # Unsigned logical max 0x91 needs a two-byte item; LANG2 is the last
        # supported array usage. Report shape remains the same eight bytes.
        self.assertIn(bytes.fromhex("1500269100050719002991"), descriptor)
        self.assertEqual(descriptor[:4], bytes([0x05, 0x01, 0x09, 0x06]))
        self.assertTrue(descriptor.endswith(bytes([0x81, 0x00, 0xC0])))
        # Exactly one 8-bit input array of six keycodes: 1 + 1 + 6 = 8 byte reports.
        self.assertIn(bytes([0x95, 0x06, 0x75, 0x08]), descriptor)

    def test_replacement_keyboard_is_created_before_the_source_is_grabbed(self):
        # Ordering is safety-critical: local keys must always have somewhere to go.
        source = (ROOT / "src/deckthere_hardware.py").read_text()
        self.assertLess(source.index("UI_DEV_CREATE"), source.index("EVIOCGRAB"))
        self.assertLess(
            source.index("Replacement keyboard did not appear"), source.index("EVIOCGRAB")
        )


class TouchKeyTests(unittest.TestCase):
    def keys(self):
        return deckthere_keyboard.TouchKeys()

    def test_a_plain_key_is_pressed_and_released(self):
        keys = self.keys()
        self.assertEqual(keys.press(4), [(4, True)])
        self.assertEqual(keys.release(4), [(4, False)])

    def test_held_shift_applies_while_held_and_does_not_latch(self):
        keys = self.keys()
        self.assertEqual(keys.press(225), [(225, True)])
        self.assertEqual(keys.press(4), [(4, True)])
        self.assertEqual(keys.release(4), [(4, False)])
        # The shift was consumed, so letting go must not latch it on.
        self.assertEqual(keys.release(225), [(225, False)])
        self.assertEqual(keys.press(4), [(4, True)])

    def test_tapping_shift_latches_it_for_exactly_one_key(self):
        keys = self.keys()
        keys.press(225)
        self.assertEqual(keys.release(225), [(225, False)])
        self.assertTrue(keys.shift_active)
        # Next key brings shift down, and releasing it clears the latch.
        self.assertEqual(keys.press(4), [(225, True), (4, True)])
        self.assertEqual(keys.release(4), [(4, False), (225, False)])
        self.assertFalse(keys.shift_active)

    def test_tapping_a_latched_shift_again_cancels_it(self):
        keys = self.keys()
        keys.press(225)
        keys.release(225)
        keys.press(225)
        keys.release(225)
        self.assertFalse(keys.shift_active)
        self.assertEqual(keys.press(4), [(4, True)])

    def test_ctrl_latches_until_tapped_again(self):
        keys = self.keys()
        keys.press(224)
        self.assertEqual(keys.release(224), [])
        self.assertEqual(keys.active, {224})
        # It stays down across several keys, which is what shortcuts need.
        self.assertEqual(keys.press(6), [(6, True)])
        self.assertEqual(keys.release(6), [(6, False)])
        self.assertEqual(keys.press(25), [(25, True)])
        self.assertEqual(keys.release(25), [(25, False)])
        keys.press(224)
        self.assertEqual(keys.release(224), [(224, False)])

    def test_altgr_latches_one_shot_and_keeps_shift_independent(self):
        keys = self.keys()
        keys.press(230)
        keys.release(230)
        self.assertTrue(keys.altgr_active)
        self.assertFalse(keys.shift_active)
        self.assertEqual(keys.press(8), [(230, True), (8, True)])
        self.assertEqual(keys.release(8), [(8, False), (230, False)])

    def test_caps_is_a_tap_that_toggles_display_only(self):
        keys = self.keys()
        self.assertEqual(keys.press(57), [(57, True), (57, False)])
        self.assertEqual(keys.release(57), [])
        self.assertTrue(keys.caps)
        self.assertEqual(keys.press(57), [(57, True), (57, False)])
        self.assertFalse(keys.caps)

    def test_labels_follow_shift_altgr_and_caps(self):
        rows = deckthere_keyboard.layout_grid("de")
        flat = {key["normal"]: key for row in rows for key in row}
        one = flat["1"]
        keys = self.keys()
        self.assertEqual(keys.label(one), "1")
        keys.press(225)
        keys.release(225)
        self.assertEqual(keys.label(one), "!")
        keys.press(4)
        keys.release(4)  # consumes the latched shift
        keys.press(230)
        keys.release(230)
        # AltGr alone wins over the base label on a German layout.
        self.assertEqual(keys.label(flat["2"]), "²")
        keys.press(8)
        keys.release(8)
        keys.press(57)  # caps on
        self.assertEqual(keys.label(flat["a"]), "A")
        # Windows German uses Shift Lock on this key, unlike the US layout.
        self.assertEqual(keys.label(one), "!")
        keys.press(225)
        self.assertEqual(keys.label(flat["a"]), "a")
        self.assertEqual(keys.label(one), "1")

    def test_decorated_keys_carry_a_label_and_active_flag(self):
        keys = self.keys()
        key = next(
            k for row in deckthere_keyboard.layout_grid("us") for k in row if k["code"] == 225
        )
        self.assertFalse(keys.decorated(key)["active"])
        keys.press(225)
        self.assertTrue(keys.decorated(key)["active"])
        self.assertEqual(keys.decorated(key)["label"], "Shift")

    def test_release_all_clears_every_modifier_for_disconnect(self):
        keys = self.keys()
        keys.press(224)
        keys.press(225)
        keys.press(230)
        self.assertEqual(keys.release_all(), [(224, False), (225, False), (230, False)])
        self.assertEqual(keys.active, set())
        self.assertEqual(keys.release_all(), [])
        self.assertEqual(keys.press(4), [(4, True)])

    def test_sync_never_repeats_an_unchanged_modifier(self):
        keys = self.keys()
        keys.press(224)
        self.assertEqual(keys.press(224), [])  # pressing an already-down modifier
        keys.release(224)  # latched
        self.assertEqual(keys.sync(), [])


if __name__ == "__main__":
    unittest.main()
