"""Unprivileged dashboard sampling; never reads VirtualHere configuration."""

import ipaddress
import os
import re
import stat
import subprocess
from pathlib import Path


def adaptive_warning(path=None):
    """Steam can omit the enabled override. Hide only for one explicit off value.

    Read as the normal user, not through the privileged backend. This is a hint,
    not a live Steam API: missing, ambiguous or unreadable settings mean unknown.
    Never expose or log any other Steam configuration content.
    """
    path = Path.home() / ".local/share/Steam/config/config.vdf" if path is None else path
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return True
            data = os.read(fd, 4 * 1024 * 1024 + 1)
        finally:
            os.close(fd)
        if len(data) > 4 * 1024 * 1024:
            return True
        values = re.findall(rb'^\s*"AdaptiveBrightnessEnabled"\s+"([^"\r\n]*)"\s*$', data, re.M)
        return values != [b"0"]
    except OSError:
        return True


def battery(root=Path("/sys/class/power_supply")):
    for supply in root.glob("*"):
        try:
            if (supply / "type").read_text().strip() != "Battery":
                continue
            scope = supply / "scope"
            if scope.exists() and scope.read_text().strip() == "Device":
                continue
            percent = int((supply / "capacity").read_text())
            if not 0 <= percent <= 100:
                continue
            state = (supply / "status").read_text().strip()
            if state not in ("Charging", "Discharging", "Full", "Not charging"):
                state = "Unknown"
            return f"{percent}%", state
        except (OSError, ValueError):
            continue
    return "--%", "Unavailable"


def command(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=1, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def address(value):
    try:
        # ipaddress permits arbitrary scope IDs; filter those before display too.
        if not re.fullmatch(r"[0-9A-Fa-f:.]+(?:%[A-Za-z0-9_.-]+)?", value):
            return None
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def network():
    local = "Unavailable"
    for family, target in (("-4", "1.1.1.1"), ("-6", "2606:4700:4700::1111")):
        fields = (command(["ip", "-o", family, "route", "get", target]) or "").split()
        if "src" in fields:
            index = fields.index("src") + 1
            if index < len(fields) and (value := address(fields[index])):
                local = value
                break
    sockets = command(["ss", "-Hnt", "state", "established", "( sport = :7575 )"])
    if sockets is None:
        return local, "Unavailable"
    peers = set()
    for line in sockets.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            peer = fields[3].rsplit(":", 1)[0].strip("[]")
            if value := address(peer):
                peers.add(value)
    return local, ", ".join(sorted(peers)) or "None"
