"""Data-only update format shared by the unprivileged downloader and installer."""

import hashlib
import io
import json
import re
import tarfile
from pathlib import PurePosixPath

SCHEMA = 1
MAX_ARCHIVE = 200 * 1024 * 1024
MAX_CONTENT = 600 * 1024 * 1024
PROTECTED = {
    "launch-mode",
    "auto-dim",
    "sleep-minutes",
    "haptics",
    "haptics-pattern",
    "session.lock",
    "update-request.json",
}


class UpdateError(Exception):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"v?[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", value
    ):
        raise UpdateError("Unsupported release version")
    return tuple(map(int, value.removeprefix("v").split(".")))


def relative(value):
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise UpdateError("Invalid update path")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise UpdateError("Unsafe update path")
    return path


def members(data):
    if len(data) > MAX_ARCHIVE:
        raise UpdateError("Update download is too large")
    result = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for entry in archive:
                name = str(relative(entry.name.rstrip("/")))
                if entry.isdir():
                    continue
                if not entry.isfile() or name in result:
                    raise UpdateError("Unsupported or duplicate archive entry")
                total += entry.size
                if total > MAX_CONTENT or len(result) >= 20000:
                    raise UpdateError("Unpacked update is too large")
                result[name] = (
                    archive.extractfile(entry).read(),
                    0o755 if entry.mode & 0o111 else 0o644,
                )
    except (tarfile.TarError, EOFError) as exc:
        raise UpdateError("Invalid update archive") from exc
    return result


def validate_bundle(data, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(data).hexdigest() != digest:
        raise UpdateError("Prepared update checksum mismatch")
    files = members(data)
    try:
        metadata = json.loads(files.pop("meta.json")[0])
        if metadata["schema"] != SCHEMA or metadata["channel"] != "stable":
            raise UpdateError("Unsupported update format")
        version(metadata["version"])
        if not re.fullmatch(r"[0-9a-f]{40}", metadata["commit"]):
            raise UpdateError("Invalid release commit")
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("Invalid update metadata") from exc
    for name in files:
        path = relative(name)
        if path.parts[0] == "root":
            if len(path.parts) != 2 or not (
                path.name.endswith((".py", ".json"))
                or path.name in ("deckthere-root", "deckthere.service")
            ):
                raise UpdateError("Unsupported system update file")
        elif path.parts[0] == "user":
            if len(path.parts) < 2 or path.parts[1] in PROTECTED or path.parts[1].startswith("."):
                raise UpdateError("Update attempts to replace saved settings")
            if path.parts[1] != "pylib" and not (
                path.suffix in (".py", ".qml", ".sh", ".json", ".png", ".rc")
                or path.name == "konsolerc"
            ):
                raise UpdateError("Unsupported user update file")
        else:
            raise UpdateError("Unsupported update destination")
    required = {
        "root/deckthere-root",
        "root/deckthere_update_install.py",
        "root/deckthere_update_common.py",
        "root/deckthere.service",
        "user/deckthere-launch.sh",
        "user/deckthere_session.py",
        "user/deckthere_update.py",
        "user/deckthere_ui.py",
    }
    if not required <= files.keys():
        raise UpdateError("Release is missing required update files")
    for name in (
        "root/deckthere-root",
        "root/deckthere_update_install.py",
        "user/deckthere-launch.sh",
    ):
        if files[name][1] != 0o755:
            raise UpdateError("Release has a non-executable launcher or installer")
    return metadata, files
