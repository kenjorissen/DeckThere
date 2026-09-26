#!/usr/bin/env python3
"""Fetch a matched, pinned private Qt runtime; no pip or system installation.

Wheel digests come from PyPI over HTTPS (transport integrity, not independent
provenance). All downloads/extraction are staged before replacing the runtime.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

VERSION = "6.11.2"
PACKAGES = ("shiboken6", "PySide6-Essentials")
API = "https://pypi.org/pypi/{package}/" + VERSION + "/json"
MAX_WHEEL = 200 * 1024 * 1024
MAX_UNPACKED = 600 * 1024 * 1024


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("Refusing non-HTTPS redirect")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(HTTPSOnly())


def compatible(python_tags, python_version):
    for tag in python_tags.split("."):
        if tag == "py3":
            return True
        if re.fullmatch(r"cp3[0-9]+", tag):
            if (3, int(tag[3:])) <= python_version:
                return True
    return False


def platform_compatible(tags, glibc):
    for tag in tags.split("."):
        match = re.fullmatch(r"manylinux_(\d+)_(\d+)_x86_64", tag)
        if match and tuple(map(int, match.groups())) <= glibc:
            return True
    return False


def select_wheel(package, python_version, glibc):
    with OPENER.open(API.format(package=package), timeout=60) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Oversized PyPI metadata")
    data = json.loads(data)
    if data["info"]["version"] != VERSION:
        raise ValueError("Unexpected Qt package version")
    candidates = []
    for entry in data["urls"]:
        name = entry["filename"]
        fields = name.removesuffix(".whl").split("-")
        if not name.endswith(".whl") or len(fields) < 5 or Path(name).name != name:
            continue
        python_tag, abi, platforms = fields[-3:]
        if abi != "abi3" or not compatible(python_tag, python_version):
            continue
        if platform_compatible(platforms, glibc):
            candidates.append(entry)
    if not candidates:
        raise ValueError(f"No compatible {package} {VERSION} wheel for this Python/glibc")
    return sorted(candidates, key=lambda entry: entry["filename"])[0]


def download(entry, destination):
    url = urllib.parse.urlsplit(entry["url"])
    if url.scheme != "https" or url.hostname != "files.pythonhosted.org":
        raise ValueError("Refusing untrusted Qt download host")
    name = entry["filename"]
    if Path(name).name != name or not name.endswith(".whl"):
        raise ValueError("Invalid wheel filename")
    expected = entry["digests"]["sha256"]
    if not re.fullmatch("[a-f0-9]{64}", expected):
        raise ValueError("Invalid wheel digest")
    target = destination / name
    digest = hashlib.sha256()
    size = 0
    with OPENER.open(entry["url"], timeout=60) as response, target.open("wb") as stream:
        while chunk := response.read(65536):
            size += len(chunk)
            if size > MAX_WHEEL:
                raise ValueError("Oversized Qt wheel")
            digest.update(chunk)
            stream.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"Checksum mismatch for {name}")
    return target


def extract(wheel, destination):
    root = destination.resolve()
    with zipfile.ZipFile(wheel) as archive:
        if sum(member.file_size for member in archive.infolist()) > MAX_UNPACKED:
            raise ValueError("Oversized unpacked Qt wheel")
        for member in archive.infolist():
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                raise ValueError("Unsafe wheel member")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Symlink in wheel")
            if not (root / path).resolve().is_relative_to(root):
                raise ValueError("Wheel member outside destination")
        archive.extractall(root)


def validate(destination):
    # Runs as the installing user, never root. Also catches missing shared libraries.
    script = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import PySide6, shiboken6
from PySide6 import QtCore, QtGui, QtQml, QtQuick
for package in (PySide6, shiboken6):
    assert Path(package.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert PySide6.__version__ == shiboken6.__version__ == sys.argv[2]
"""
    subprocess.run(
        [sys.executable, "-I", "-c", script, str(destination), VERSION], check=True, timeout=30
    )


def install(destination):
    if platform.machine() != "x86_64" or sys.version_info < (3, 10):
        raise ValueError("Qt requires x86-64 Linux and Python 3.10+")
    libc, version = platform.libc_ver()
    if libc != "glibc":
        raise ValueError("Cannot determine glibc compatibility")
    glibc = tuple(map(int, version.split(".")[:2]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("Refusing symlinked Qt destination")
    with tempfile.TemporaryDirectory(prefix=".deckthere-qt-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "runtime"
        stage.mkdir()
        records = []
        for package in PACKAGES:
            entry = select_wheel(package, sys.version_info[:2], glibc)
            print(f"Fetching {package} {VERSION}", flush=True)
            wheel = download(entry, Path(temporary))
            extract(wheel, stage)
            records.append({"filename": entry["filename"], "sha256": entry["digests"]["sha256"]})
        validate(stage)
        (stage / "deckthere-runtime.json").write_text(
            json.dumps({"version": VERSION, "wheels": records})
        )
        backup = Path(temporary) / "previous"
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(stage, destination)
        except BaseException:
            if backup.exists():
                os.replace(backup, destination)
            raise
    print(f"Qt {VERSION} ready in {destination}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=Path.home() / ".local/share/deckthere/pylib"
    )
    parser.add_argument(
        "--check", action="store_true", help="validate an existing runtime without downloading"
    )
    options = parser.parse_args(argv)
    if os.geteuid() == 0:
        parser.error("Run as your normal user, never with sudo")
    if options.check:
        validate(options.destination)
    else:
        install(options.destination)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Qt runtime installation failed: {exc}") from exc
