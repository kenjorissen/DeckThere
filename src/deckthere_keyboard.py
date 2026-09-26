"""USB HID state and bundled, sourced layout/IME legends (no GUI dependencies)."""

import json
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("deckthere_layouts.json").read_text(encoding="utf-8"))
CATALOG = {
    identity: {**entry, "keys": _DATA["mappings"][entry["mapping"]]}
    for identity, entry in _DATA["layouts"].items()
}
LAYOUT_NAMES = {identity: entry["name"] for identity, entry in CATALOG.items()}
INTERNATIONAL_KEYS = {135, 136, 137, 138, 139, 144, 145}
ALLOWED_KEYS = (
    set(range(4, 70)) | {76, 79, 80, 81, 82, 100} | INTERNATIONAL_KEYS | set(range(224, 232))
)


class KeyState:
    def __init__(self):
        self.keys = set()

    def update(self, code, down):
        if type(code) is not int or code not in ALLOWED_KEYS or type(down) is not bool:
            raise ValueError("Unsupported key event")
        if down:
            if code < 224 and code not in self.keys and sum(k < 224 for k in self.keys) >= 6:
                raise ValueError("Maximum six simultaneous non-modifier keys")
            self.keys.add(code)
        else:
            self.keys.discard(code)
        return self.report()

    def clear(self):
        self.keys.clear()
        return self.report()

    def report(self):
        modifiers = sum(1 << (key - 224) for key in self.keys if key >= 224)
        keys = sorted(key for key in self.keys if key < 224)
        return bytes([modifiers, 0, *keys, *([0] * (6 - len(keys)))])


def layout_rows(layout):
    """Legends only: the PC still chooses output, composition and candidate text."""
    if layout not in CATALOG:
        raise ValueError("Unknown keyboard layout")
    entry = CATALOG[layout]
    geometry = entry["geometry"]
    labels = entry["keys"]
    special = {
        40: "Enter",
        41: "Esc",
        42: "⌫",
        43: "Tab",
        44: "Space",
        57: "Caps",
        76: "Del",
        79: "→",
        80: "←",
        81: "↓",
        82: "↑",
        224: "Ctrl",
        225: "Shift",
        226: "Alt",
        227: "Super",
        228: "Ctrl",
        229: "Shift",
        230: "AltGr",
        136: "かな",
        138: "変換",
        139: "無変換",
        144: "한/영",
        145: "한자",
    }
    special.update({code: f"F{code - 57}" for code in range(58, 70)})
    if geometry == "korean-104":
        special.update({230: "한/영", 228: "한자"})
    iso = geometry in ("iso", "abnt2")
    codes = [
        [53, *range(30, 40), 45, 46, *([137] if geometry == "jis" else []), 42],
        [43, 20, 26, 8, 21, 23, 28, 24, 12, 18, 19, 47, 48, 50 if iso or geometry == "jis" else 49],
        [57, 4, 22, 7, 9, 10, 11, 13, 14, 15, 51, 52, 40],
        [
            225,
            *([100] if iso else []),
            29,
            27,
            6,
            25,
            5,
            17,
            16,
            54,
            55,
            56,
            *([135] if geometry in ("abnt2", "jis") else []),
            229,
        ],
        [
            224,
            227,
            226,
            230,
            *([139] if geometry == "jis" else []),
            44,
            *([138, 136] if geometry == "jis" else []),
            *([144, 145] if geometry == "korean" else []),
            80,
            81,
            82,
            79,
            228,
        ],
        [41, *range(58, 70)],
    ]
    rows = []
    for row in codes:
        keys = []
        for code in row:
            if str(code) not in labels and code not in special:
                raise ValueError(f"Missing legend for {layout}: HID {code}")
            legend = labels.get(str(code), {"labels": [special.get(code, "?")] * 8, "dead": 0})
            normal, shift, altgr = legend["labels"][:3]
            weight = 5 if code == 44 else 1.6 if code in (40, 42, 57, 225, 229) else 1
            keys.append(
                {
                    "code": code,
                    "normal": normal,
                    "shift": shift,
                    "altgr": altgr if str(code) in labels else "",
                    "labels": legend["labels"],
                    "dead": legend["dead"],
                    "overlay": entry.get("overlays", {}).get(str(code), []),
                    "weight": weight,
                    "letter": len(normal) == len(shift) == 1
                    and normal.isalpha()
                    and shift.isalpha(),
                }
            )
        rows.append(keys)
    return rows


MODIFIERS = set(range(224, 232))
# Shift and AltGr apply to exactly one following key; the rest stay latched until
# tapped again, which is what makes shortcuts usable one finger at a time.
ONE_SHOT = {225, 229, 230}
STICKY = {224, 226, 227, 228, 231}
CAPS = 57


class TouchKeys:
    """Turn touch down/up events into the exact key events to send to the PC.

    Pure logic: it never touches hardware, so the awkward parts (one-shot versus
    latched modifiers, caps display, releasing everything on disconnect) are
    directly testable.
    """

    def __init__(self):
        self.held = set()
        self.sticky = set()
        self.oneshot = set()
        self.temporary = set()
        self.used = set()
        self.caps = False
        self.applied = set()

    # -- state exposed to the UI ------------------------------------------
    @property
    def active(self):
        """Every modifier the user has engaged, including latched one-shots."""
        return self.held | self.sticky | self.oneshot | self.temporary

    @property
    def wire(self):
        """Modifiers that must currently be held down on the PC.

        A latched one-shot is deliberately absent until the next real key press,
        so tapping Shift does not leave Shift down on the remote machine.
        """
        return self.held | self.sticky | self.temporary

    @property
    def shift_active(self):
        return bool(self.active & {225, 229})

    @property
    def altgr_active(self):
        return 230 in self.active

    def label(self, key):
        """The character this key would produce right now, for display only.

        The PC's own layout/IME decides actual output. Caps is local tracking,
        not a query of the PC's state. Secondary IME legends are reminders only.
        """
        index = int(self.shift_active) | (int(self.altgr_active) << 1) | (int(self.caps) << 2)
        label = key["labels"][index] or "—"
        if key["dead"] & (1 << index):
            label += "◌"
        if key["overlay"] and not self.altgr_active:
            label += "\n" + key["overlay"][int(self.shift_active)]
        return label

    def decorated(self, key):
        return {
            "code": key["code"],
            "span": key["span"],
            "label": self.label(key),
            "active": key["code"] in self.active or (key["code"] == CAPS and self.caps),
        }

    # -- input ------------------------------------------------------------
    def press(self, code):
        if code in MODIFIERS:
            self.held.add(code)
            self.used.discard(code)
            return self.sync()
        if code == CAPS:
            # A tap, not a hold: the PC latches caps itself.
            self.caps = not self.caps
            return [(CAPS, True), (CAPS, False)]
        self.temporary = set(self.oneshot)
        self.used |= self.held
        return [*self.sync(), (code, True)]

    def release(self, code):
        if code in MODIFIERS:
            self.held.discard(code)
            if code in self.used:
                self.used.discard(code)
            elif code in ONE_SHOT:
                self.oneshot.symmetric_difference_update({code})
            else:
                self.sticky.symmetric_difference_update({code})
            return self.sync()
        if code == CAPS:
            return []
        self.temporary.clear()
        self.oneshot.clear()
        return [(code, False), *self.sync()]

    def sync(self):
        """Emit only the modifier transitions needed to match the desired state."""
        desired = self.wire
        events = [(code, True) for code in sorted(desired - self.applied)]
        events += [(code, False) for code in sorted(self.applied - desired)]
        self.applied = set(desired)
        return events

    def release_all(self):
        self.held.clear()
        self.sticky.clear()
        self.oneshot.clear()
        self.temporary.clear()
        self.used.clear()
        events = [(code, False) for code in sorted(self.applied)]
        self.applied = set()
        return events


def largest_remainder(weights, total):
    """Split `total` integer cells across weights, never giving a key zero cells."""
    if total < len(weights):
        raise ValueError("not enough cells for every key")
    exact = [weight * total / sum(weights) for weight in weights]
    spans = [int(value) for value in exact]
    remaining = total - sum(spans)
    order = sorted(range(len(weights)), key=lambda index: (-(exact[index] - spans[index]), index))
    for index in order[:remaining]:
        spans[index] += 1
    return spans


def layout_grid(layout, columns=1000):
    """Lay every row out across the same number of cells.

    A large cell count keeps key widths visually proportional while keeping touch
    hit-testing exact integer arithmetic: cell = x * columns // width.
    """
    grid = []
    for row in layout_rows(layout):
        spans = largest_remainder([key["weight"] for key in row], columns)
        grid.append([{**key, "span": span} for key, span in zip(row, spans)])
    return grid
