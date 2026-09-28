import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # Discover imports tests/ as top-level; reach source modules.

import deckthere_ipc  # noqa: E402
import deckthere_keyboard  # noqa: E402


class EncodeDecodeTests(unittest.TestCase):
    def test_round_trip_for_every_operation(self):
        messages = (
            {"op": "status"},
            {"op": "clear"},
            {"op": "ping"},
            {"op": "stop"},
            {"op": "keyboard_start"},
            {"op": "keyboard_stop"},
            {"op": "auto_dim", "enabled": True},
            {"op": "auto_dim", "enabled": False},
            {"op": "key", "code": 4, "down": True},
            {"op": "key", "code": 225, "down": False},
            {"op": "layout", "layout": "de"},
        )
        for message in messages:
            with self.subTest(message=message):
                line = deckthere_ipc.encode(message)
                self.assertTrue(line.endswith(b"\n"))
                self.assertEqual(line.count(b"\n"), 1)
                self.assertEqual(deckthere_ipc.decode(line[:-1]), message)

    def test_encoded_lines_stay_within_the_frame_limit(self):
        worst = deckthere_ipc.encode({"op": "key", "code": 255, "down": False})
        self.assertLess(len(worst), deckthere_ipc.MAX_LINE)

    def test_unknown_operations_and_shapes_are_rejected(self):
        for message in (
            "status",
            4,
            None,
            [],
            {},
            {"op": 4},
            {"op": "shutdown"},
            {"op": "exec", "cmd": "id"},
            {"op": "key"},
        ):
            with self.subTest(message=message):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate(message)

    def test_missing_extra_and_mistyped_fields_are_rejected(self):
        for message in (
            {"op": "auto_dim"},
            {"op": "auto_dim", "enabled": 1},
            {"op": "auto_dim", "enabled": "true"},
            {"op": "auto_dim", "enabled": True, "path": "/tmp/brightness"},
            {"op": "key", "code": 4},  # missing down
            {"op": "key", "down": True},  # missing code
            {"op": "status", "shared": True},  # unknown field
            {"op": "key", "code": 4, "down": True, "repeat": 3},  # unknown field
            {"op": "keyboard_stop", "path": "/sys/kernel/config"},  # no supplied paths
            {"op": "keyboard_start", "remember": True},  # no root startup preferences
            {"op": "layout"},  # missing layout
            {"op": "layout", "layout": 7},  # wrong type
            {"op": "key", "code": "4", "down": True},  # wrong type
            {"op": "status", "op2": "stop"},  # unknown field
        ):
            with self.subTest(message=message):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate(message)

    def test_bool_is_not_accepted_where_an_int_is_required(self):
        with self.assertRaises(deckthere_ipc.ProtocolError):
            deckthere_ipc.validate({"op": "key", "code": True, "down": True})

    def test_int_is_not_accepted_where_a_bool_is_required(self):
        for down in (1, 0, "true", None):
            with self.subTest(down=down):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate({"op": "key", "code": 4, "down": down})

    def test_only_real_keyboard_usages_and_known_layouts_are_accepted(self):
        for code in (-1, 0, 3, 71, 78, 83, 101, 223, 232, 999):
            with self.subTest(code=code):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate({"op": "key", "code": code, "down": True})
        for layout in ("", "US", "not-a-layout", "us; rm -rf /", "de\n"):
            with self.subTest(layout=layout):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate({"op": "layout", "layout": layout})

    def test_invalid_json_and_encoding_are_rejected(self):
        for line in (
            b"",
            b"{",
            b"null",
            b"[]",
            b'{"op":"status"} trailing',
            b'{"op":"status"}\x00',
            b"\xff\xfe",
        ):
            with self.subTest(line=line):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.decode(line)

    def test_deeply_nested_json_is_rejected_rather_than_crashing(self):
        for decode in (deckthere_ipc.decode, deckthere_ipc.decode_response):
            with (
                self.subTest(decode=decode.__name__),
                self.assertRaises(deckthere_ipc.ProtocolError),
            ):
                decode(b"[" * 2000 + b"]" * 2000)

    def test_parser_recursion_errors_are_normalized_on_every_python_version(self):
        # Some Python versions parse the deep fixture and then reject its shape;
        # others raise while parsing. Cover that exception path deterministically.
        with patch.object(deckthere_ipc.json, "loads", side_effect=RecursionError):
            for decode in (deckthere_ipc.decode, deckthere_ipc.decode_response):
                with (
                    self.subTest(decode=decode.__name__),
                    self.assertRaises(deckthere_ipc.ProtocolError),
                ):
                    decode(b"[]")


class ReaderTests(unittest.TestCase):
    def test_splits_batches_and_reassembles_partial_frames(self):
        reader = deckthere_ipc.Reader()
        self.assertEqual(reader.feed(b'{"op":"ping"}\n{"op":"st'), [b'{"op":"ping"}'])
        self.assertEqual(reader.feed(b'atus"}\n'), [b'{"op":"status"}'])
        self.assertEqual(reader.feed(b"\n"), [b""])

    def test_oversized_frame_and_oversized_partial_are_rejected(self):
        reader = deckthere_ipc.Reader(limit=16)
        with self.assertRaises(deckthere_ipc.ProtocolError):
            reader.feed(b"x" * 17)
        reader = deckthere_ipc.Reader(limit=16)
        with self.assertRaises(deckthere_ipc.ProtocolError):
            reader.feed(b"x" * 17 + b"\n")

    def test_a_large_batch_of_small_frames_is_accepted(self):
        # Bounded per frame, not per read: a burst of key events must not fail.
        reader = deckthere_ipc.Reader(limit=64)
        batch = deckthere_ipc.encode({"op": "key", "code": 4, "down": True}) * 50
        lines = reader.feed(batch)
        self.assertEqual(len(lines), 50)
        for line in lines:
            self.assertEqual(deckthere_ipc.decode(line), {"op": "key", "code": 4, "down": True})


class CrossModuleTests(unittest.TestCase):
    def test_protocol_usages_and_layouts_match_the_keyboard_module(self):
        self.assertEqual(deckthere_ipc.KEY_CODES, deckthere_keyboard.ALLOWED_KEYS)
        self.assertEqual(set(deckthere_ipc.LAYOUTS), set(deckthere_keyboard.LAYOUT_NAMES))

    def test_every_layout_key_can_be_sent_over_the_protocol(self):
        for name in deckthere_ipc.LAYOUTS:
            with self.subTest(layout=name):
                for row in deckthere_keyboard.layout_rows(name):
                    for key in row:
                        deckthere_ipc.validate({"op": "key", "code": key["code"], "down": True})

    def test_protocol_messages_never_carry_paths_or_commands(self):
        # The backend must never receive a filename, device path, or shell text.
        for message in (
            {"op": "type", "text": "hello"},
            {"op": "run", "path": "/bin/sh"},
            {"op": "brightness", "absolute": 50},
        ):
            with self.subTest(message=message):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate(message)
        self.assertNotIn(
            "text", {field for fields in deckthere_ipc.OPERATIONS.values() for field in fields}
        )
        self.assertNotIn(
            "path", {field for fields in deckthere_ipc.OPERATIONS.values() for field in fields}
        )


class ResponseTests(unittest.TestCase):
    def status(self, **overrides):
        message = {
            "op": "status",
            "shared": True,
            "keyboard": True,
            "stopping": False,
            "auto_dim": True,
            "percent": 1,
            "layout": "de",
            "keys": 0,
        }
        message.update(overrides)
        return message

    def test_a_well_formed_status_and_pong_are_accepted(self):
        self.assertEqual(deckthere_ipc.validate_response(self.status()), self.status())
        self.assertEqual(deckthere_ipc.validate_response({"op": "pong"}), {"op": "pong"})

    def test_status_is_rejected_when_fields_are_missing_extra_or_mistyped(self):
        broken = [
            {key: value for key, value in self.status().items() if key != "percent"},
            self.status(keys="0"),
            self.status(shared=1),
            self.status(keyboard=1),
            self.status(unexpected=True),
            {"op": "status"},
        ]
        for message in broken:
            with self.subTest(message=message):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate_response(message)

    def test_status_ranges_and_layout_are_checked(self):
        for overrides in (
            {"percent": -1},
            {"percent": 101},
            {"percent": 1.0},
            {"keys": -1},
            {"keys": 11},
            {"layout": "dvorak"},
            {"layout": ""},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(deckthere_ipc.ProtocolError):
                    deckthere_ipc.validate_response(self.status(**overrides))

    def test_requests_are_not_valid_responses_and_the_reverse(self):
        # The two schemas must not be interchangeable.
        with self.assertRaises(deckthere_ipc.ProtocolError):
            deckthere_ipc.validate_response({"op": "key", "code": 4, "down": True})
        with self.assertRaises(deckthere_ipc.ProtocolError):
            deckthere_ipc.validate(self.status())

    def test_decode_response_rejects_invalid_json(self):
        with self.assertRaises(deckthere_ipc.ProtocolError):
            deckthere_ipc.decode_response(b"not json")
        self.assertEqual(deckthere_ipc.decode_response(b'{"op":"pong"}'), {"op": "pong"})

    def test_the_backend_status_shape_matches_the_response_schema(self):
        # Guards against the backend and the UI drifting apart.
        source = (ROOT / "src/deckthere_backend.py").read_text()
        for field in deckthere_ipc.RESPONSES["status"]:
            self.assertIn(f'"{field}":', source)


if __name__ == "__main__":
    unittest.main()
