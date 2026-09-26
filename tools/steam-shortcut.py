#!/usr/bin/env python3
"""Add DeckThere to Steam's shortcuts.vdf without third-party dependencies."""

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path


# Preserve original field types, order, and bytes, including unrelated shortcuts.
def decode(data):
    pos = 0

    def string():
        nonlocal pos
        end = data.index(b"\0", pos)
        value = data[pos:end]
        pos = end + 1
        return value

    def obj(depth=0):
        nonlocal pos
        if depth > 64:
            raise ValueError("VDF nesting is too deep")
        fields = []
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == 8:
                return fields
            key = string()
            if kind == 0:
                value = obj(depth + 1)
            elif kind == 1:
                value = string()
            elif kind in (2, 3, 4, 7):
                size = 8 if kind == 7 else 4
                value = data[pos : pos + size]
                if len(value) != size:
                    raise ValueError("Truncated VDF value")
                pos += size
            else:
                raise ValueError(f"Unsupported VDF type {kind}; file not changed")
            fields.append((kind, key, value))
        raise ValueError("Missing VDF end marker")

    result = obj()
    if pos != len(data):
        raise ValueError("Unexpected trailing VDF data")
    return result


def encode(fields):
    return (
        b"".join(
            bytes([kind])
            + key
            + b"\0"
            + (encode(value) if kind == 0 else value + b"\0" if kind == 1 else value)
            for kind, key, value in fields
        )
        + b"\x08"
    )


def text(key, value):
    return (1, key.encode(), value.encode())


def number(key, value):
    return (2, key.encode(), struct.pack("<I", value))


def update(data, install_dir, mode="gui"):
    if mode not in ("gui", "keyboard", "terminal"):
        raise ValueError("Invalid interface mode")
    root = decode(data) if data else [(0, b"shortcuts", [])]
    containers = [v for t, k, v in root if t == 0 and k == b"shortcuts"]
    if len(containers) != 1:
        raise ValueError("Expected one shortcuts object")
    entries = containers[0]
    matches = []
    for i, (kind, key, fields) in enumerate(entries):
        if kind != 0:
            raise ValueError("Unexpected non-object shortcut")
        values = {k: v for t, k, v in fields if t == 1}
        # Also adopt the existing manually-created shortcut, regardless of name.
        if (
            values.get(b"appname", b"").lower() == b"deckthere"
            or b"/deckthere.sh" in values.get(b"LaunchOptions", b"")
            or b"/deckthere.sh" in values.get(b"exe", b"")
            or b"/deckthere-gui.sh" in values.get(b"LaunchOptions", b"")
            or b"/deckthere-gui.sh" in values.get(b"exe", b"")
            or b"/deckthere-launch.sh" in values.get(b"LaunchOptions", b"")
            or b"/deckthere-launch.sh" in values.get(b"exe", b"")
        ):
            matches.append(i)
    if len(matches) > 1:
        raise ValueError("Multiple DeckThere shortcuts found; remove duplicates in Steam first")
    path = str(install_dir)
    if any(c in path for c in '\n\r\0"\\`$'):
        raise ValueError("Installation path contains unsupported launch-option characters")
    desired = [
        text("appname", "DeckThere"),
        text("exe", '"/usr/bin/env"'),
        text("StartDir", f'"{path}"'),
        text(
            "LaunchOptions",
            f'-u LD_PRELOAD "{path}/deckthere-launch.sh"',
        ),
        number("AllowOverlay", 1),
    ]
    icon = install_dir / "artwork/icon.png"
    default_icon = str(icon) if icon.is_file() else ""
    if matches:
        index = matches[0]
        kind, key, fields = entries[index]
        if default_icon and not any(k == b"icon" and v for _, k, v in fields):
            desired.append(text("icon", default_icon))
        replacements = {k: (t, k, v) for t, k, v in desired}
        updated = [replacements.pop(k, (t, k, v)) for t, k, v in fields]
        updated.extend(replacements.values())
        # Keep appid and other user settings (including artwork associations).
        entries[index] = (kind, key, updated)
    else:
        used = {k for _, k, _ in entries}
        index = 0
        while str(index).encode() in used:
            index += 1
        appid = zlib.crc32(b'"/usr/bin/env"DeckThere') | 0x80000000
        fields = (
            [number("appid", appid)]
            + desired
            + [
                text("icon", default_icon),
                text("ShortcutPath", ""),
                number("IsHidden", 0),
                number("AllowDesktopConfig", 1),
                number("OpenVR", 0),
                number("Devkit", 0),
                text("DevkitGameID", ""),
                number("DevkitOverrideAppID", 0),
                number("LastPlayTime", 0),
                text("FlatpakAppID", ""),
                (0, b"tags", []),
            ]
        )
        entries.append((0, str(index).encode(), fields))
    return encode(root)


def steam_running():
    for proc in Path("/proc").glob("[0-9]*/comm"):
        try:
            if proc.read_text().strip().lower() in ("steam", "steam.exe", "steamwebhelper"):
                return True
        except (OSError, UnicodeError):
            pass
    return False


def ensure_steam_closed():
    if not steam_running():
        return
    instruction = "Fully exit Steam first (Steam > Exit in Desktop Mode), then rerun"
    if not sys.stdin.isatty():
        raise ValueError(instruction)
    print("Steam is running. Save your games and finish any downloads first.")
    print("Run this from Desktop Mode; closing Steam in Gaming Mode can end your session.")
    try:
        answer = input("Shut down Steam gracefully to update the shortcut? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        raise ValueError("Shutdown cancelled; shortcut not changed") from None
    if answer.strip().lower() not in ("y", "yes"):
        raise ValueError("Steam left running; shortcut not changed. " + instruction)
    steam = shutil.which("steam")
    if steam is None:
        raise ValueError("Steam command not found. " + instruction)
    print("Requesting Steam shutdown...")
    try:
        subprocess.run(
            [steam, "-shutdown"],
            check=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Could not request Steam shutdown. " + instruction) from exc
    deadline = time.monotonic() + 30
    while steam_running():
        if time.monotonic() >= deadline:
            raise ValueError(
                "Steam did not exit within 30 seconds; shortcut not changed. " + instruction
            )
        time.sleep(0.5)
    print("Steam has exited.")


def offer_start_steam():
    if not sys.stdin.isatty() or steam_running():
        return
    try:
        answer = input("Open Steam now to see the DeckThere shortcut? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\nSteam left closed. Open it when ready.")
        return
    if answer.strip().lower() not in ("y", "yes"):
        print("Steam left closed. Open it when ready.")
        return
    steam = shutil.which("steam")
    if steam is None:
        print("Steam command not found; open Steam manually.")
        return
    try:
        subprocess.Popen(
            [steam],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("Steam launch requested.")
    except OSError as exc:
        print(f"Shortcut is ready, but Steam could not be launched: {exc}")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        fd, backup = tempfile.mkstemp(
            prefix=f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-", dir=path.parent
        )
        os.close(fd)
        shutil.copy2(path, backup)
        print(f"Backup: {backup}")
    fd, temporary = tempfile.mkstemp(prefix=".deckthere-shortcuts-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_artwork(data, install_dir, config_dir):
    """Fill missing Steam library art using the saved app ID; never replace custom art."""
    root = decode(data)
    entries = next(fields for kind, key, fields in root if kind == 0 and key == b"shortcuts")
    fields = next(fields for _, _, fields in entries if (1, b"appname", b"DeckThere") in fields)
    appid = next((value for kind, key, value in fields if kind == 2 and key == b"appid"), None)
    if appid is None:
        return 0
    identity = struct.unpack("<I", appid)[0]
    assets = install_dir / "artwork"
    grid = config_dir / "grid"
    installed = 0
    for name, suffix in (
        ("portrait", "p"),
        ("landscape", ""),
        ("hero", "_hero"),
        ("logo", "_logo"),
    ):
        source = assets / f"{name}.png"
        if not source.is_file():
            continue
        grid.mkdir(parents=True, exist_ok=True)
        stem = f"{identity}{suffix}"
        if any(path.stem == stem for path in grid.iterdir()):
            continue  # Includes other formats, symlinks and existing custom assets.
        destination = grid / f"{stem}.png"
        fd, temporary = tempfile.mkstemp(prefix=".deckthere-artwork-", dir=grid)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)  # Atomic publication without overwriting.
            except FileExistsError:
                continue
            installed += 1
        finally:
            Path(temporary).unlink(missing_ok=True)
    return installed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", help="numeric Steam userdata directory name")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--gui", dest="mode", action="store_const", const="gui")
    modes.add_argument("--keyboard", dest="mode", action="store_const", const="keyboard")
    modes.add_argument("--terminal", dest="mode", action="store_const", const="terminal")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check account/shortcut location without changing files or stopping Steam",
    )
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run as your normal user, not with sudo")
    roots = [Path.home() / ".local/share/Steam", Path.home() / ".steam/steam"]
    root = next((p for p in roots if (p / "userdata").is_dir()), None)
    if root is None:
        parser.error("Steam userdata not found; log in to Steam once first")
    accounts = sorted(
        p.name
        for p in (root / "userdata").iterdir()
        if p.is_dir() and p.name.isdigit() and p.name != "0"
    )
    if args.account:
        if args.account not in accounts:
            parser.error("Unknown account; available userdata IDs: " + ", ".join(accounts))
        account = args.account
    elif len(accounts) == 1:
        account = accounts[0]
    else:
        parser.error("Choose --account ID from userdata IDs: " + ", ".join(accounts))
    path = root / "userdata" / account / "config/shortcuts.vdf"
    if args.check:
        print(f"Steam account ready: {account}; shortcuts: {path}")
        return
    install_dir = Path.home() / ".local/share/deckthere"
    # One shortcut follows preferences; changing the UI never changes its identity.
    module_dir = Path(__file__).resolve().parent
    if not (module_dir / "deckthere_preferences.py").is_file():
        module_dir = module_dir.parent / "src"
    sys.path.insert(0, str(module_dir))
    from deckthere_preferences import read_mode, save_mode

    mode = args.mode or read_mode(install_dir / "launch-mode")
    for name in ("deckthere.sh", "deckthere-gui.sh", "deckthere-launch.sh"):
        launcher = install_dir / name
        if not launcher.is_file() or not os.access(launcher, os.X_OK):
            parser.error(
                f"Installed launcher missing or not executable: {launcher}; run setup.sh first"
            )
    try:
        ensure_steam_closed()
    except ValueError as exc:
        parser.error(str(exc))
    original = path.read_bytes() if path.exists() else b""
    changed = update(original, install_dir, mode)
    if steam_running():
        parser.error("Steam started during setup; shortcut not changed")
    if changed != original:
        save(path, changed)
    artwork_count = install_artwork(changed, install_dir, path.parent)
    if args.mode:
        save_mode(install_dir / "launch-mode", mode)
    if artwork_count:
        print(
            f"Installed {artwork_count} missing Steam artwork images; existing artwork preserved."
        )
    if changed == original:
        print("DeckThere shortcut is already up to date.")
    else:
        print(f"DeckThere shortcut installed for account {account} ({mode}).")
    print("After restarting Steam, find it in Gaming Mode: Library > Non-Steam > DeckThere.")
    print("It may not appear on Home / Recently Played until you launch it.")
    offer_start_steam()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RecursionError) as exc:
        raise SystemExit(f"Cannot update Steam shortcut: {exc}") from exc
