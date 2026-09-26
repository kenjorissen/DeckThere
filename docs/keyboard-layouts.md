# Keyboard layouts, familiar names, and their limits

DeckThere's touchscreen keyboard is for occasional typing while using the Deck as a
controller: search, chat, a short command, or a text field. It is **not a complete
international input-method system** and does not replace the PC's keyboard or IME.

> **Author's note:** I want DeckThere to work for people using every language
> and keyboard layout. I don't own all of these keyboards or have the ability to
> test every layout, operating system, and input method. Broad support is the
> goal, not a claim that I've personally tested everything. Bug reports,
> corrections, and reports of successful configurations are very welcome.

## Choosing a layout

1. Activate your usual keyboard layout or input method **on the PC**.
2. On the Deck, tap the small **Layout: …** control. Scroll the chooser; a local
   keyboard can also type into its name filter. No local keyboard is required
   just to scroll and select.
3. Select the name and variant you recognize, read its note, then tap **DONE**.
4. Try a few non-sensitive characters in a plain text editor on the PC.

Selection is saved at `/home/.deckthere/data/keyboard-layout`, alongside the other
private service settings. It survives sessions, setup, and normal uninstall;
`--purge-settings` deletes it. An absent/invalid preference falls back to US.
It is one saved choice for this installation—not a per-PC profile or detection
result. Change it when using a PC/application with a different active layout.
A save failure is reported in the service journal.

## What actually goes over the connection

DeckThere sends standard USB HID **key positions and modifier bits**, not Unicode text.
The PC interprets those positions using its active layout, keyboard driver,
application, and input method. VirtualHere transports the USB device; it does not
translate text for DeckThere. The controller and virtual keyboard remain separate USB
devices; [VirtualHere licensing requirements](../README.md#virtualhere-licensing)
are unchanged.

Selecting a layout changes the Deck's key legends and the available key positions.
It **does not select, install, or reconfigure the PC's layout/IME**. The connection
does not report the resulting characters or the PC's active layout, so there is
no automatic detection, reliable probing, or automatic following of per-window
layout changes. There is no PC companion program, clipboard integration, or
arbitrary Unicode/emoji injection.

## Why some familiar names share a mapping

People look for their layout or input method by name, so the chooser keeps those
names even when their key positions/legends are identical. The catalog stores
identical mappings once and reuses them. For example, the basic Windows Swedish
and Finnish tables share legends. Several Chinese input-method profiles share
standard US QWERTY positions, with different explanatory notes.

An **IME** badge means a matching input method must already be enabled on the PC.
Such an entry is a convenient label/legend profile, **not an IME implemented by
DeckThere**. Multiple names do not imply different functionality or separately verified
host support.

## Included choices

The catalog contains 56 named layouts/profiles backed by 42
shared legend mappings. This is practical coverage, **not a researched ranking
of the world's most-used layouts**, and it is not exhaustive.

| Family | Choices |
| --- | --- |
| English | US, US International, US Dvorak; UK, UK Extended |
| French | Legacy AZERTY, Standard AZERTY, BÉPO, Canadian French |
| Belgian | Period AZERTY, Comma AZERTY |
| German / Swiss | German QWERTZ, Swiss German, Swiss French |
| Spanish | Spain, Latin American |
| Portuguese | Portugal, Brazil ABNT2 |
| Italian | Standard, 142 |
| Nordic | Swedish, Finnish, Norwegian, Danish |
| Polish | Programmers, 214 QWERTZ |
| Czech | QWERTZ, QWERTY, Programmers |
| Hungarian | QWERTZ, 101-key |
| Turkish | Q, F |
| Cyrillic | Russian ЙЦУКЕН, Russian Typewriter; Ukrainian, Ukrainian Enhanced |
| Greek | Standard, Polytonic |
| Arabic | 101, 102, 102 AZERTY |
| Japanese | JIS Romaji, JIS Kana legends |
| Korean | 2-set with 103/106-key hardware; 2-set with 101/104-key Type 1 mapping |
| Simplified Chinese | Pinyin, Shuangpin / Double Pinyin, Wubi 86, Wubi 98, Wubi New Century |
| Traditional Chinese | Zhuyin / Bopomofo, Pinyin, Cangjie, Quick / Sucheng, Cantonese Jyutping |

### Chinese, Japanese, and Korean

- **Simplified Chinese:** Pinyin, Shuangpin, and Wubi entries use Latin QWERTY
  legends. The PC's IME determines syllable assignments, radical decomposition,
  Wubi version, script, punctuation, and candidates. We do not pretend a generic
  legend can describe every Shuangpin or Wubi scheme.
- **Traditional Chinese:** the standard Taiwan Zhuyin profile adds Bopomofo key
  reminders. Cangjie and Quick add shared key-root reminders for users including
  Taiwan, Hong Kong, and Macao. They do not implement decomposition or predict
  candidates. Traditional Pinyin and Jyutping use Latin legends and require a
  suitable PC IME. Alternative Zhuyin arrangements are not covered by the standard
  Taiwan legend profile.
- **Japanese:** use the PC's JIS hardware mapping plus the intended Romaji or Kana
  input setting. The JIS profiles include the extra international key positions
  and conversion keys. Kana legends do not describe every IME mode or resulting
  composed character.
- **Korean:** 2-set profiles show Hangul key reminders. The 103/106-key profile
  offers dedicated Hangul/Hanja usages; 101/104 Type 1 treats Right Alt and Right
  Ctrl as tap-only IME commands. Other Windows hardware types and 3-set layouts
  are not interchangeable with these profiles.

Composition, candidate lists, and committed text remain **on the PC**. The Deck
cannot display them or know whether a conversion succeeded. Ordinary Space,
Enter, arrow, number and function keys can operate the PC IME where it supports
them; this is not a dedicated candidate-selection UI.

## Important limitations

- **The published mapping tables are Windows references.** Matching base layouts
  often overlap across systems, but Linux/XKB, macOS, custom layouts, Option/AltGr,
  dead keys, Caps behavior, and applications can differ. Do not assume that a
  shared language name means an identical mapping on every OS.
- **Table-derived does not mean hardware-tested.** Hardware coverage is limited.
  The layout variants, modifiers, IME profiles, and ABNT2/JIS/Korean-specific
  USB usages have not all been tested
  end-to-end on real PCs. Automated tests check data, report shape, geometry,
  selection and persistence—not what every host ultimately types.
- Shift, AltGr, Shift+AltGr and locally tracked Caps combinations have sourced
  previews. Caps is **not synchronized with the PC's LEDs/state**; external Caps
  changes or a new session can make the preview wrong. Releasing keys does not
  turn the PC's Caps Lock off.
- **◌ marks a dead key**: the PC composes the accent with a subsequent character.
  A label is not a promise that tapping it immediately inserts that character.
  Blank character states display **—**; they are not guessed from the base key.
- Modifier taps have convenience behavior: Shift/AltGr can be one-shot and
  Ctrl/Alt/Super can latch. Standalone modifier taps can also trigger **PC IME
  shortcuts** (for example, Shift switching input modes). Hold a modifier for
  ordinary chording where needed, and use **RELEASE KEYS** to clear held/latched
  modifiers. Match/configure the PC's hotkeys yourself.
- The UI is not an exact replica of every physical keyboard: Enter is flattened,
  there is no full numeric keypad, and it uses six-key rollover plus modifiers.
  Missing keypad/OEM keys, specialized layouts, accessibility behavior, macros,
  and advanced IME workflows may need a real keyboard.
- Fonts installed on the Deck determine glyph availability and shaping. A missing
  glyph, clipped label, or ambiguous legend is a bug report worth sending.
- Output goes to **whatever application has focus on the PC**. Test in a harmless
  text editor first; DeckThere cannot see or verify the destination or resulting text.

## Reporting a problem or requesting a variant

Open an issue at <https://github.com/kenjorissen/DeckThere/issues>. Include:

1. DeckThere commit/version and Deck model/SteamOS version.
2. PC OS/version, exact active keyboard-layout name, and physical-keyboard type
   where relevant (ANSI/ISO/ABNT2/JIS/Korean Type 1, etc.).
3. PC IME name/version and input mode, if used; include a Shuangpin/Wubi/Cangjie
   variant where relevant.
4. Selected DeckThere layout/profile, key(s)/modifiers tapped, expected output, and
   actual output in a plain text editor. Check Caps Lock and use RELEASE KEYS
   before making a small repeatable test.
5. Whether it is a **wrong label**, **missing/mis-sized key**, **wrong typed
   character**, **font issue**, or **IME interaction**. A screenshot can help.

For a missing layout, provide its exact name and a trustworthy layout table or
reference. For a success report, tell us the same host/layout/IME combination so
we can distinguish confirmed combinations from untested data.

**Do not include passwords, license keys, private VirtualHere config, or other
sensitive typed text.** Redact addresses and personal content from screenshots
and logs. Use non-sensitive examples to reproduce problems.

## Data and maintenance

`src/deckthere_layouts.json` contains bundled legends plus source URLs and SHA-256 records.
`tools/build-layouts.py` reads the processing XML published by
[kbdlayout.info](https://kbdlayout.info/), using stable Windows layout identifiers
and the JIS `kbd106` table. It downloads **XML, not Windows DLLs**. These are
keyboard-mapping facts, not a redistributed driver or an endorsement by Microsoft
or VirtualHere. A recorded digest identifies the input data; it is not an
independent signature or evidence of hardware compatibility.

Secondary IME legends are small, separately maintained reminders of standard
[Bopomofo](https://en.wikipedia.org/wiki/Bopomofo),
[Cangjie](https://en.wikipedia.org/wiki/Cangjie_input_method), and
[Korean 2-set](https://en.wikipedia.org/wiki/Keyboard_layout#Hangul_(for_Korean))
arrangements. They are not complete dictionaries or input-method engines.

Developer regeneration (review the resulting diff and run both test suites):

```bash
python3 tools/build-layouts.py --cache /tmp/deckthere-layout-tables --fetch
# Rebuild from the same cached XML without network access:
python3 tools/build-layouts.py --cache /tmp/deckthere-layout-tables
```

Existing cache files are reused; retain the cache if exact regeneration is
needed. Fetch into a new cache to review upstream changes. Runtime layout
selection is entirely local: **no catalog downloads, cloud service, typing
telemetry, or host fingerprinting**.
