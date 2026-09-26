#!/usr/bin/env python3
"""Build bundled factual key legends from kbdlayout.info's Windows-layout XML.

Development-only. Downloads XML, never DLLs or executable code. Runtime/install
uses the committed catalog; no network requests are made to select a layout.
"""

import argparse
import hashlib
import json
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# Stable Windows layout IDs, except kbd106 (the actual JIS table, not the stub).
LAYOUTS = [
    ("us", "English (US)", "00000409", "ansi"),
    ("us-intl", "English (US International)", "00020409", "ansi"),
    ("us-dvorak", "English (US Dvorak)", "00010409", "ansi"),
    ("uk", "English (UK)", "00000809", "iso"),
    ("uk-ext", "English (UK Extended)", "00000452", "iso"),
    ("fr", "French / Français — Legacy AZERTY", "0000040c", "iso"),
    ("fr-std", "French / Français — Standard AZERTY", "0001040c", "iso"),
    ("fr-bepo", "French / Français — BÉPO", "0002040c", "iso"),
    ("ca-fr", "French / Français — Canadian", "00001009", "iso"),
    ("be", "Belgian — Period AZERTY", "00000813", "iso"),
    ("be-comma", "Belgian — Comma AZERTY", "0001080c", "iso"),
    ("de", "German / Deutsch — QWERTZ", "00000407", "iso"),
    ("ch-de", "Swiss German — QWERTZ", "00000807", "iso"),
    ("ch-fr", "Swiss French — QWERTZ", "0000100c", "iso"),
    ("es", "Spanish / Español — Spain", "0000040a", "iso"),
    ("latam", "Spanish / Español — Latin American", "0000080a", "iso"),
    ("pt", "Portuguese / Português — Portugal", "00000816", "iso"),
    ("br", "Portuguese / Português — Brazil ABNT2", "00010416", "abnt2"),
    ("it", "Italian / Italiano", "00000410", "iso"),
    ("it-142", "Italian / Italiano — 142", "00010410", "iso"),
    ("se", "Swedish / Svenska", "0000041d", "iso"),
    ("fi", "Finnish / Suomi", "0000040b", "iso"),
    ("no", "Norwegian / Norsk", "00000414", "iso"),
    ("dk", "Danish / Dansk", "00000406", "iso"),
    ("pl", "Polish / Polski — Programmers", "00000415", "ansi"),
    ("pl-214", "Polish / Polski — 214 QWERTZ", "00010415", "iso"),
    ("cz", "Czech / Čeština — QWERTZ", "00000405", "iso"),
    ("cz-qwerty", "Czech / Čeština — QWERTY", "00010405", "iso"),
    ("cz-prog", "Czech / Čeština — Programmers", "00020405", "ansi"),
    ("hu", "Hungarian / Magyar — QWERTZ", "0000040e", "iso"),
    ("hu-101", "Hungarian / Magyar — 101-key", "0001040e", "ansi"),
    ("tr", "Turkish / Türkçe — Q", "0000041f", "iso"),
    ("tr-f", "Turkish / Türkçe — F", "0001041f", "iso"),
    ("ru", "Russian / Русский — ЙЦУКЕН", "00000419", "ansi"),
    ("ru-typewriter", "Russian / Русский — Typewriter", "00010419", "ansi"),
    ("ua", "Ukrainian / Українська", "00000422", "ansi"),
    ("ua-enhanced", "Ukrainian / Українська — Enhanced", "00020422", "ansi"),
    ("gr", "Greek / Ελληνικά", "00000408", "iso"),
    ("gr-polytonic", "Greek / Ελληνικά — Polytonic", "00060408", "iso"),
    ("ar", "Arabic / العربية — 101", "00000401", "ansi"),
    ("ar-102", "Arabic / العربية — 102", "00010401", "iso"),
    ("ar-azerty", "Arabic / العربية — 102 AZERTY", "00020401", "iso"),
    ("jp", "Japanese / 日本語 — JIS, Romaji", "kbd106", "jis"),
]
# Flags: bit 0 Shift, bit 1 AltGr (Windows Ctrl+Alt), bit 2 Caps Lock.
STATES = [
    frozenset(
        (
            (["VK_SHIFT"] if index & 1 else [])
            + (["VK_CONTROL", "VK_MENU"] if index & 2 else [])
            + (["VK_CAPITAL"] if index & 4 else [])
        )
    )
    for index in range(8)
]
SCANCODES = {0x29: 53, 0x0C: 45, 0x0D: 46, 0x2B: 49, 0x56: 100, 0x73: 135, 0x7D: 137}
SCANCODES.update(zip(range(2, 12), range(30, 40)))
SCANCODES.update(zip(range(0x10, 0x1C), [20, 26, 8, 21, 23, 28, 24, 12, 18, 19, 47, 48]))
SCANCODES.update(zip(range(0x1E, 0x29), [4, 22, 7, 9, 10, 11, 13, 14, 15, 51, 52]))
SCANCODES.update(zip(range(0x2C, 0x36), [29, 27, 6, 25, 5, 17, 16, 54, 55, 56]))


def value(result):
    dead = result.find("DeadKeyTable")
    if dead is not None:
        return dead.get("Accent", ""), True
    text = result.get("Text")
    if text is None:
        text = "".join(chr(int(code, 16)) for code in result.get("TextCodepoints", "").split())
    return (text if text.isprintable() else ""), False


def parse_keys(data, geometry, kana=False):
    keys = {}
    root = ET.fromstring(data)
    if root.tag != "KeyboardLayout":
        raise ValueError("Expected layout XML")
    for physical in root.findall("./PhysicalKeys/PK"):
        scan = int(physical.get("SC"), 16)
        if scan not in SCANCODES:
            continue
        code = SCANCODES[scan]
        if code == 49 and geometry != "ansi":
            code = 50
        if code == 100 and geometry in ("ansi", "jis"):
            continue
        if code == 135 and geometry not in ("jis", "abnt2"):
            continue
        if code == 137 and geometry != "jis":
            continue
        states = {
            frozenset(result.get("With", "").split()): value(result)
            for result in physical.findall("Result")
        }
        labels, dead_mask = [], 0
        for index, state in enumerate(STATES):
            target = state | {"VK_KANA"} if kana else state
            result = states.get(target)
            if result is None and index & 4:
                result = states.get(target - {"VK_CAPITAL"})
            # No AltGr/Shift+AltGr output really means no character. Don't display
            # an invented fallback character; use a dash for an empty legend.
            if result is None:
                result = (physical.get("Name", "") if not states else "", False)
            text, dead = result
            if kana:
                text = unicodedata.normalize("NFKC", text)
            labels.append(text)
            if dead:
                dead_mask |= 1 << index
        keys[str(code)] = {"labels": labels, "dead": dead_mask}
    required = set(range(4, 30)) | set(range(30, 40)) | {45, 46, 47, 48, 51, 52, 53, 54, 55, 56}
    if not required <= {int(code) for code in keys}:
        raise ValueError("Missing main keyboard positions")
    return keys


def build(cache):
    layouts = {}
    for identity, name, source_id, geometry in LAYOUTS:
        source = f"https://kbdlayout.info/{source_id}/download/xml"
        data = (cache / f"{source_id}.xml").read_bytes()
        layouts[identity] = {
            "name": name,
            "geometry": geometry,
            "kind": "layout",
            "source": source,
            "sha256": hashlib.sha256(data).hexdigest(),
            "note": "Match the PC's active layout and exact variant. Legends follow published Windows tables; other OS mappings can differ. ◌ marks a dead key composed by the PC.",
            "keys": parse_keys(data, geometry),
        }
    layouts["jp"]["kind"] = "ime"
    layouts["jp"]["note"] = (
        "Requires a PC Japanese IME configured for JIS hardware and Romaji input. This does not enable the IME. Composition/candidates remain on the PC."
    )
    layouts["jp-kana"] = {
        **layouts["jp"],
        "name": "Japanese / 日本語 — JIS, Kana legends",
        "note": "Requires JIS hardware mapping and Kana input in the PC IME, not Romaji. Labels are key legends, not a prediction of composed text. Candidates remain on the PC.",
        "keys": parse_keys((cache / "kbd106.xml").read_bytes(), "jis", kana=True),
    }
    # IME input uses standard US positions, not fabricated Unicode HID reports.
    profiles = [
        (
            "ko",
            "Korean / 한국어 — 2-set, 103/106-key",
            "korean",
            "PC IME: Korean 2-set with dedicated Hangul/Hanja keys. Other Korean arrangements are not inferred.",
        ),
        (
            "ko-104",
            "Korean / 한국어 — 2-set, 101/104 Type 1",
            "korean-104",
            "PC IME: Korean 2-set, 101-key Type 1 (Right Alt = Hangul; Right Ctrl = Hanja). Other hardware types differ.",
        ),
        (
            "zh-pinyin",
            "Chinese 简体 — Pinyin (Simplified)",
            "ansi",
            "PC IME: Simplified Chinese Pinyin. Latin key legends; no local candidate window.",
        ),
        (
            "zh-shuangpin",
            "Chinese 简体 — Shuangpin / Double Pinyin",
            "ansi",
            "Configure the chosen Double Pinyin scheme on the PC. Latin legends do not describe every scheme's syllable assignments.",
        ),
        (
            "zh-wubi86",
            "Chinese 简体 — Wubi 86",
            "ansi",
            "PC IME: Wubi 86. Latin key positions only; radical/decomposition rules are handled by the PC.",
        ),
        (
            "zh-wubi98",
            "Chinese 简体 — Wubi 98",
            "ansi",
            "PC IME: Wubi 98. Latin key positions only; this does not select or configure Wubi on the PC.",
        ),
        (
            "zh-wubi-new",
            "Chinese 简体 — Wubi New Century",
            "ansi",
            "PC IME: Wubi New Century. Latin key positions only; the PC handles composition and candidates.",
        ),
        (
            "zh-tw-zhuyin",
            "Chinese 繁體 — Zhuyin / Bopomofo",
            "ansi",
            "PC IME: Traditional Chinese Zhuyin with the standard Taiwan arrangement. Alternative Zhuyin arrangements need different labels.",
        ),
        (
            "zh-tw-pinyin",
            "Chinese 繁體 — Pinyin (Traditional)",
            "ansi",
            "PC IME: Traditional Chinese Pinyin (including Taiwan users). Latin key positions; the PC controls the output script.",
        ),
        (
            "zh-cangjie",
            "Chinese 繁體 — Cangjie / 倉頡",
            "ansi",
            "PC IME: Cangjie (Taiwan/Hong Kong/Macao). Key-root reminders only; the installed IME determines its Cangjie version and candidates.",
        ),
        (
            "zh-quick",
            "Chinese 繁體 — Quick / 速成",
            "ansi",
            "PC IME: Quick/Sucheng (Taiwan/Hong Kong/Macao). Shares Cangjie key roots; completion is entirely on the PC.",
        ),
        (
            "zh-jyutping",
            "Chinese 繁體 — Cantonese Jyutping",
            "ansi",
            "Requires a PC IME supporting Jyutping. Latin legends only; DeckThere does not install or configure a Cantonese IME.",
        ),
    ]
    for identity, name, geometry, note in profiles:
        layouts[identity] = {
            **layouts["us"],
            "name": name,
            "geometry": geometry,
            "kind": "ime",
            "note": note
            + " Choosing this profile changes Deck labels only; activate the matching input method on the PC.",
        }
    # Secondary legends: the Latin/number position remains visible above them.
    korean = dict(
        zip("qwertyuiopasdfghjklzxcvbnm", "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ")
    )
    shifted_korean = {**korean, **dict(zip("qwertop", "ㅃㅉㄸㄲㅆㅒㅖ"))}
    zhuyin = dict(
        zip(
            "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-63 47",
            "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦˊˇ ˋ˙",
        )
    )
    cangjie = dict(
        zip("qwertyuiopasdfghjklxcvbnm", "手田水口廿卜山戈人心日尸木火土竹十大中難金女月弓一")
    )
    for identity, normal, shifted in (
        ("ko", korean, shifted_korean),
        ("ko-104", korean, shifted_korean),
        ("zh-tw-zhuyin", zhuyin, zhuyin),
        ("zh-cangjie", cangjie, cangjie),
        ("zh-quick", cangjie, cangjie),
    ):
        overlays = {}
        for code, key in layouts["us"]["keys"].items():
            letter = key["labels"][0]
            if letter in normal and normal[letter].strip():
                overlays[code] = [normal[letter], shifted[letter]]
        layouts[identity] = {**layouts[identity], "overlays": overlays}
    # Many familiar profile names intentionally share identical legend data.
    # Store each mapping once; keep the names/notes people recognize separately.
    mappings, signatures = {}, {}
    for identity, entry in layouts.items():
        keys = entry.pop("keys")
        signature = json.dumps(keys, sort_keys=True)
        if signature not in signatures:
            signatures[signature] = identity
            mappings[identity] = keys
        entry["mapping"] = signatures[signature]
    return {"schema": 2, "mappings": mappings, "layouts": layouts}


def write_catalog(catalog, path):
    # One key per line keeps generated data reviewable instead of expanding each
    # eight-state legend array into ten lines. json.dumps still escapes all data.
    lines = ["{", '  "schema": 2,', '  "mappings": {']
    for index, (identity, mapping) in enumerate(catalog["mappings"].items()):
        lines.append(f"    {json.dumps(identity)}: {{")
        for number, (code, key) in enumerate(mapping.items()):
            comma = "," if number + 1 < len(mapping) else ""
            lines.append(f"      {json.dumps(code)}: {json.dumps(key, ensure_ascii=False)}{comma}")
        comma = "," if index + 1 < len(catalog["mappings"]) else ""
        lines.append("    }" + comma)
    lines.append("  },")
    entries = json.dumps(catalog["layouts"], ensure_ascii=False, indent=2).splitlines()
    lines.append('  "layouts": ' + entries[0])
    lines.extend("  " + line for line in entries[1:])
    lines.append("}")
    text = "\n".join(lines) + "\n"
    assert json.loads(text) == catalog
    path.write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/deckthere_layouts.json",
    )
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    if args.fetch:
        for _, _, source_id, _ in LAYOUTS:
            target = args.cache / f"{source_id}.xml"
            if target.exists():
                continue
            print(f"Fetching {source_id}", flush=True)
            with urllib.request.urlopen(
                f"https://kbdlayout.info/{source_id}/download/xml", timeout=30
            ) as response:
                data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("Oversized layout XML")
            ET.fromstring(data)
            target.write_bytes(data)
    catalog = build(args.cache)
    write_catalog(catalog, args.output)
    print(f"Wrote {len(catalog['layouts'])} layouts/profiles to {args.output}")


if __name__ == "__main__":
    main()
