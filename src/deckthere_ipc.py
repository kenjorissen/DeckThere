"""Line-delimited JSON protocol between the unprivileged UI and the root backend.

The UI is a normal user process. Everything privileged (USB gadget, key grab,
brightness) lives in the backend, so this module is the whole attack surface
between them. It therefore validates strictly: known operation, known fields,
exact types and known key codes only. Nothing here touches hardware.
"""

import json

from deckthere_keyboard import ALLOWED_KEYS, LAYOUT_NAMES

MAX_LINE = 4096
OPERATIONS = {
    # op -> {field: type}
    "status": {},
    "keyboard_start": {},
    "keyboard_stop": {},
    "key": {"code": int, "down": bool},
    "clear": {},
    "layout": {"layout": str},
    "ping": {},
    "stop": {},
}
LAYOUTS = tuple(LAYOUT_NAMES)
# Replies from the root backend to the UI. Validated just as strictly as requests
# so a malformed or unexpected reply can never be silently trusted.
RESPONSES = {
    "status": {
        "shared": bool,
        "keyboard": bool,
        "stopping": bool,
        "percent": int,
        "layout": str,
        "keys": int,
    },
    "pong": {},
    "keyboard_error": {},
}
# HID usages present on a keyboard: letters/digits/punctuation/function,
# navigation, and the eight modifiers.
KEY_CODES = ALLOWED_KEYS


class ProtocolError(Exception):
    pass


def encode(message):
    """Return one protocol line. Caller must have validated the message."""
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def _validate(message, schema, kind):
    """Return a normalized copy of a decoded message, or raise ProtocolError."""
    if type(message) is not dict:
        raise ProtocolError(f"{kind} must be an object")
    op = message.get("op")
    if type(op) is not str or op not in schema:
        raise ProtocolError(f"unknown {kind}")
    fields = schema[op]
    extra = set(message) - {"op"} - set(fields)
    if extra:
        raise ProtocolError(f"unexpected {kind} field")
    for name, expected in fields.items():
        if name not in message:
            raise ProtocolError(f"missing {kind} field {name}")
        # bool is a subclass of int, so an exact type check is required.
        if type(message[name]) is not expected:
            raise ProtocolError(f"{kind} field {name} has the wrong type")
    return message


def validate(message):
    """Validate a UI request."""
    message = _validate(message, OPERATIONS, "request")
    op = message["op"]
    if op == "key" and message["code"] not in KEY_CODES:
        raise ProtocolError("unsupported key code")
    if op == "layout" and message["layout"] not in LAYOUTS:
        raise ProtocolError("unsupported layout")
    return message


def validate_response(message):
    """Validate a backend reply."""
    message = _validate(message, RESPONSES, "response")
    if message["op"] == "status":
        if not 0 <= message["percent"] <= 100:
            raise ProtocolError("status percent out of range")
        if not 0 <= message["keys"] <= 10:
            raise ProtocolError("status key count out of range")
        if message["layout"] not in LAYOUTS:
            raise ProtocolError("status layout is unknown")
    return message


def decode(line):
    """Validate one raw request line (without its newline)."""
    return _parse(line, validate)


def decode_response(line):
    """Validate one raw reply line (without its newline)."""
    return _parse(line, validate_response)


def _parse(line, validator):
    try:
        message = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("invalid JSON") from exc
    return validator(message)


class Reader:
    """Incremental line reader that bounds memory and rejects oversized input."""

    def __init__(self, limit=MAX_LINE):
        self.limit = limit
        self.buffer = b""

    def feed(self, data):
        self.buffer += data
        lines = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if len(line) > self.limit:
                raise ProtocolError("message too long")
            lines.append(line)
        # Only the as-yet unterminated remainder is unbounded input.
        if len(self.buffer) > self.limit:
            raise ProtocolError("message too long")
        return lines
