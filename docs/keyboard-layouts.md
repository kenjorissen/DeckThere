# Keyboard layouts

DeckThere's touch keyboard is for occasional typing while using the Deck as a
controller. It sends USB HID **key positions and modifiers**, not Unicode text.
The PC's layout, keyboard driver, application, and input method determine the
result. VirtualHere transports the device; it does not translate characters.

## Choosing a layout

1. Activate the intended layout or input method **on the PC**.
2. In the Deck's GUI, tap **Layout: …**. Scroll or use a local keyboard to type in
   the name filter; a local keyboard is not required to select an entry.
3. Select the exact variant, read its note, and tap **DONE**.
4. Try non-sensitive characters in a plain text editor on the PC.

Selection changes the Deck's legends and available key positions. **It does not
install, select, or configure the PC's layout/IME.** There is no host-layout
detection, per-window tracking, clipboard integration, or Unicode/emoji injection.

The choice is saved at `/home/.deckthere/data/keyboard-layout` and survives setup
and normal uninstall; `--purge-settings` removes it. Missing/invalid preferences
fall back to US. It is one installation-wide choice, not a per-PC profile. Change
it when the receiving PC/application uses a different layout. Save failures are
reported in the service journal.

Enable and connect the virtual keyboard as described in the
[GUI controls](../README.md#gui-and-touch-keyboard). Controller and keyboard are
separate devices; [VirtualHere licensing](../README.md#virtualhere-licensing) applies.

## Included choices

The catalog has **56 named layouts/profiles and 42 shared legend mappings**.
Familiar names are kept even where mappings match: for example, basic Windows
Swedish and Finnish share legends, and several Chinese profiles use US QWERTY
positions with different notes.

An **IME** badge means a matching input method must be enabled on the PC. It is a
label/legend profile, not an input-method engine implemented by DeckThere.

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

- **Simplified Chinese:** Pinyin, Shuangpin, and Wubi use Latin QWERTY legends.
  The PC IME determines syllables, radicals, Wubi version, script, punctuation,
  and candidates. Generic legends cannot describe every scheme.
- **Traditional Chinese:** standard Taiwan Zhuyin adds Bopomofo reminders;
  Cangjie and Quick add key-root reminders. These do not implement decomposition
  or prediction. Traditional Pinyin and Jyutping use Latin legends and require
  a suitable IME. Alternative Zhuyin arrangements are not covered by the
  standard Taiwan profile.
- **Japanese:** use the PC's JIS hardware mapping and intended Romaji/Kana input
  setting. Profiles include international positions and conversion keys; Kana
  legends do not describe every IME mode or composed result.
- **Korean:** 2-set profiles show Hangul reminders. The 103/106-key profile has
  dedicated Hangul/Hanja usages; 101/104 Type 1 treats Right Alt and Right Ctrl
  as tap-only IME commands. Other hardware types and 3-set layouts are not
  interchangeable with these profiles.

Composition, candidates, and committed text stay **on the PC**. Space, Enter,
arrows, numbers, and function keys can control the IME where supported, but the
Deck cannot show candidates or know whether conversion succeeded.

## Modifiers and legends

- Tap **Shift/AltGr** for a one-shot modifier; **Ctrl/Alt/Super** latch until tapped
  again. Hold modifiers for ordinary chording. Modifier taps can also trigger
  PC IME shortcuts, so configure the PC's hotkeys accordingly.
- **RELEASE KEYS**, hiding the keyboard, opening a modal panel, or losing focus
  clears held/latched input. Caps Lock is tracked locally and is **not synchronized
  with the PC's LEDs/state**. Clearing keys does not turn Caps Lock off; an external
  Caps change or a new session can make the preview wrong.
- Legends include Shift, AltGr, Shift+AltGr, and locally tracked Caps combinations.
  **◌ marks a dead key** whose accent is composed by the PC with a later character.
  Blank states display **—**, rather than a guessed base character.
- Keys highlight while touched. Modifier/Caps highlights also show their latched
  state; they are not confirmation of text received by the PC.

## Limitations

- **Mappings use published Windows tables.** Linux/XKB, macOS, custom layouts,
  Option/AltGr, dead keys, Caps behavior, and applications may differ even when
  language names match.
- **Coverage is not exhaustive hardware validation.** Profiles and extended USB
  usages have not all been tested end-to-end on real PCs. Automated checks cover
  data, reports, geometry, selection, and persistence—not every host's output.
- The UI is not an exact physical keyboard: Enter is flattened, there is no full
  numeric keypad, and rollover is six ordinary keys plus modifiers. Missing OEM/
  keypad keys, macros, accessibility features, and advanced IME workflows may
  require a real keyboard.
- Available fonts determine glyph coverage and shaping. Report missing glyphs,
  clipped labels, or ambiguous legends.
- Output goes to **whatever application has focus on the PC**. DeckThere cannot
  inspect the destination or verify the resulting text.

Corrections, additional variants, and reports of working configurations are welcome.

## Reporting a problem or requesting a variant

Open an issue at <https://github.com/kenjorissen/DeckThere/issues> with:

1. DeckThere commit/version, Deck model, and SteamOS version.
2. PC OS/version, exact active layout, and physical-keyboard type where relevant
   (ANSI/ISO/ABNT2/JIS/Korean Type 1, etc.).
3. PC IME/version and input mode, including any Shuangpin/Wubi/Cangjie variant.
4. Selected DeckThere profile, keys/modifiers used, expected output, and actual
   output in a plain text editor. Check Caps and use RELEASE KEYS before testing.
5. Whether the problem is a wrong label, missing/mis-sized key, wrong character,
   font issue, or IME interaction. Screenshots can help.

For a missing layout, include an exact name and trustworthy mapping reference.
For a working configuration, include the same host/layout/IME details so confirmed
combinations can be distinguished from untested data.

**Use non-sensitive examples.** Do not include passwords, license keys, private
config, or sensitive typed text. Redact addresses and personal content in images
and logs.

## Data and maintenance

`src/deckthere_layouts.json` contains legends, source URLs, and SHA-256 records.
`tools/build-layouts.py` processes XML from [kbdlayout.info](https://kbdlayout.info/)
using Windows layout identifiers and the JIS `kbd106` table. It downloads **XML,
not Windows DLLs**. These are mapping facts, not redistributed drivers or an
endorsement by Microsoft or VirtualHere. Digests identify source data; they are
not independent signatures or proof of hardware compatibility.

Secondary legends are separately maintained reminders of standard
[Bopomofo](https://en.wikipedia.org/wiki/Bopomofo),
[Cangjie](https://en.wikipedia.org/wiki/Cangjie_input_method), and
[Korean 2-set](https://en.wikipedia.org/wiki/Keyboard_layout#Hangul_(for_Korean))
arrangements, not dictionaries or IME engines.

To regenerate during development:

```bash
python3 tools/build-layouts.py --cache /tmp/deckthere-layout-tables --fetch
# Rebuild from the cached XML without network access:
python3 tools/build-layouts.py --cache /tmp/deckthere-layout-tables
```

Retain the cache for repeatable regeneration; existing files are reused. Fetch
into a new cache to review upstream changes. Inspect the diff and run the relevant
catalog, keyboard/IPC, and Qt checks described in [Development](development.md).
Runtime selection is local: **no catalog downloads, cloud service, typing
telemetry, or host fingerprinting**.
