"""Unprivileged dashboard sampling; never reads VirtualHere configuration."""

import ipaddress
import re
import subprocess
from pathlib import Path


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
