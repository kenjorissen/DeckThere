#!/usr/bin/env python3
"""Normal-user inactivity policy, shared by GUI and terminal supervisors.

No privileged commands except the existing service stop performed by the caller.
Suspend is requested only after successful cleanup and an inactive, successful
service. State is session-local; only sleep-minutes is a retained preference.
"""

import json
import math
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHOICES = (0, 5, 15, 30, 60)
WARNING_SECONDS = 30
BASE = Path(__file__).resolve().parent
SOCKET = "/run/deckthere/activity.sock"
STATE = "sleep-state"


def read_data(path, default=None):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return default
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        if len(raw) > 4096:
            return default
        return json.loads(raw)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return default


def save_data(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".sleep-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_minutes(base=BASE):
    value = read_data(base / "sleep-minutes", 0)
    return value if type(value) is int and value in CHOICES else 0


def save_minutes(minutes, base=BASE):
    if type(minutes) is not int or minutes not in CHOICES:
        raise ValueError("Unsupported sleep interval")
    save_data(base / "sleep-minutes", minutes)
    activity(base)


def activity(base=BASE):
    save_data(base / "sleep-activity", time.monotonic())


def stamp(value, now):
    return type(value) in (float, int) and 0 <= value <= now and math.isfinite(value)


def initialize(base=BASE, now=None):
    now = time.monotonic() if now is None else now
    value = dict(
        minutes=0,
        last=now,
        tick=now,
        warning=None,
        countdown=None,
        status="off",
        remaining=0,
        message="",
    )
    save_data(base / STATE, value)
    return value


def snapshot(now):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.4)
        connection.connect(SOCKET)
        _, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != 0:
            raise OSError("Untrusted activity server")
        raw = b""
        while b"\n" not in raw and len(raw) <= 256:
            data = connection.recv(257 - len(raw))
            if not data:
                break
            raw += data
        if len(raw) > 256 or not raw.endswith(b"\n"):
            raise ValueError("Invalid activity snapshot")
        value = json.loads(raw)
        if (
            type(value) is not dict
            or set(value) != {"healthy", "activity"}
            or (
                type(value["healthy"]) is not bool or not stamp(value["activity"], time.monotonic())
            )
        ):
            raise ValueError("Invalid activity snapshot")
        return value


def tick(base=BASE, now=None, observe=snapshot):
    live_clock = now is None
    now = time.monotonic() if live_clock else now
    old = read_data(base / STATE)
    fields = {"minutes", "last", "tick", "warning", "countdown", "status", "remaining", "message"}
    if (
        not isinstance(old, dict)
        or set(old) != fields
        or any(not stamp(old.get(key), now) for key in ("last", "tick"))
        or any(
            old.get(key) is not None and not stamp(old[key], now)
            for key in ("warning", "countdown")
        )
    ):
        old = initialize(base, now)
    minutes = read_minutes(base)
    reset = minutes != old["minutes"] or now - old["tick"] > 2.5
    current = dict(old, minutes=minutes, tick=now, status="off", remaining=0, message="")
    local = read_data(base / "sleep-activity", 0)
    local = local if stamp(local, now) else now  # Invalid cancellation data fails awake.
    healthy = False
    latest = now
    if minutes:
        try:
            observed = observe(now)
            sampled_at = time.monotonic() if live_clock else now
            healthy = observed["healthy"] is True and stamp(observed["activity"], sampled_at)
            # The observer can see input while this request is in flight. Keep
            # the saved state internally ordered without calling that a fault.
            latest = min(observed["activity"], now) if healthy else now
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            healthy = False
    if not minutes or not healthy or reset:
        current.update(last=now, warning=None, countdown=None)
        if minutes:
            current.update(
                status="waiting", message="Auto sleep: monitoring unavailable — staying awake"
            )
    else:
        latest = max(local, latest)
        if latest > current["last"]:
            current.update(last=latest, warning=None, countdown=None)
        current.update(status="armed", message=f"Auto sleep: {minutes} min idle")
        if now - current["last"] >= minutes * 60:
            if current["warning"] is None:
                current["warning"] = now
            acknowledgement = read_data(base / "sleep-warning-seen", {})
            seen = (
                isinstance(acknowledgement, dict)
                and acknowledgement.get("warning") == current["warning"]
                and stamp(acknowledgement.get("at"), now)
                and now - acknowledgement["at"] <= 2
            )
            if seen and current["countdown"] is None:
                current["countdown"] = acknowledgement["at"]
            if not seen and now - current["warning"] > 2:
                current.update(
                    last=now,
                    warning=None,
                    countdown=None,
                    status="waiting",
                    message="Auto sleep: warning unavailable — staying awake",
                )
            else:
                remaining = (
                    WARNING_SECONDS
                    if current["countdown"] is None
                    else max(0, math.ceil(WARNING_SECONDS - (now - current["countdown"])))
                )
                current.update(
                    status="warning" if remaining else "due",
                    remaining=remaining,
                    message=f"Sleep in {remaining}s — any input cancels",
                )
    if minutes or old["status"] != "off" or old["minutes"] != 0:
        save_data(base / STATE, current)
    return current


def visible_state(base=BASE):
    value = read_data(base / STATE, {})
    now = time.monotonic()
    if (
        not isinstance(value, dict)
        or not stamp(value.get("tick"), now)
        or now - value["tick"] > 2.5
    ):
        return {}
    return value


def warning_shown(base=BASE):
    value = visible_state(base)
    if value.get("status") == "warning":
        save_data(
            base / "sleep-warning-seen", {"warning": value["warning"], "at": time.monotonic()}
        )


def pending_sleep(base):
    value = read_data(base / STATE, {})
    now = time.monotonic()
    local = read_data(base / "sleep-activity", 0)
    if (
        not isinstance(value, dict)
        or value.get("status") != "due"
        or (
            not stamp(value.get("tick"), now)
            or now - value["tick"] > 30
            or read_minutes(base) != value.get("minutes")
            or not read_minutes(base)
            or not stamp(local, now)
            or local > value["tick"]
        )
    ):
        return False
    return True


def suspend_after_cleanup(base=BASE, run=subprocess.run, cancelled=None):
    """Call only after successful cleanup. No sudo or inhibitor override."""
    if not pending_sleep(base) or (cancelled is not None and cancelled()):
        return False
    check = run(
        ["/usr/bin/systemctl", "show", "deckthere.service", "-p", "ActiveState", "-p", "Result"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    properties = dict(line.split("=", 1) for line in check.stdout.splitlines() if "=" in line)
    if (
        check.returncode
        or properties.get("ActiveState") != "inactive"
        or properties.get("Result") != "success"
    ):
        return False
    # A cancellation or signal during the bounded service query still wins.
    if not pending_sleep(base) or (cancelled is not None and cancelled()):
        return False
    # Consume first: returning from suspend, refusal or a crash must never retry.
    initialize(base)
    result = run(["/usr/bin/systemctl", "--no-ask-password", "suspend"], timeout=20, check=False)
    return result.returncode == 0


def main():
    if os.geteuid() == 0:
        raise SystemExit("Sleep policy runs as the normal user")
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action == "init":
        initialize()
    elif action == "tick":
        value = tick()
        print("SLEEP_DUE" if value["status"] == "due" else value["message"])
    elif action == "activity":
        activity()
    elif action == "shown":
        warning_shown()
    elif action == "suspend":
        if not suspend_after_cleanup():
            raise SystemExit("Automatic sleep not performed; sharing has stopped.")
    else:
        raise SystemExit("Expected init, tick, activity, shown or suspend")


if __name__ == "__main__":
    main()
