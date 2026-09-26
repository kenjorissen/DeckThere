#!/usr/bin/env python3
"""Normal-user Gamescope activity pulse; no input events or saved setting changes.

Steam's Gaming Mode idle policy does not honor logind inhibition before starting
its suspend animation. Gamescope publishes this counter for Steam; changing it
has been tested to reset both dim and sleep timers. This is an undocumented
workaround, not a screensaver/inhibition protocol. Never restore an old counter:
it is live compositor metadata, and real input may have advanced it meanwhile.
"""

import json
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ATOM = "GAMESCOPE_INPUT_COUNTER"
INTERVAL = 10
DISPLAY_PATTERN = re.compile(r":(?:0|[1-9][0-9]{0,2})(?:\.0)?\Z")
COUNTER_PATTERN = re.compile(r"GAMESCOPE_INPUT_COUNTER\(CARDINAL\) = ([0-9]{1,10})\n?\Z")
DISABLE_VARIABLE = "DECKTHERE_DISABLE_GAMESCOPE_IDLE"


class IdleError(RuntimeError):
    """Keepalive unavailable; don't silently run unprotected in Gaming Mode."""


def gaming_mode():
    # Do not affect Desktop Mode or an unrelated/remote X11 desktop.
    return "gamescope" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")


def command(arguments, display=None):
    environment = dict(os.environ, LC_ALL="C")
    if display is not None:
        environment["DISPLAY"] = display
    try:
        result = subprocess.run(
            arguments,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise IdleError("Gamescope idle command unavailable or timed out") from exc
    if result.returncode:
        raise IdleError("Gamescope idle command failed")
    return result.stdout


def process_identity(pid):
    """PID + start time pins the compositor; no protected /proc/exe access."""
    try:
        proc = Path("/proc") / str(pid)
        argv0 = (proc / "cmdline").read_bytes().split(b"\0", 1)[0]
        if argv0 not in (b"gamescope", b"/usr/bin/gamescope"):
            raise ValueError("not Gamescope")
        uids = next(
            line.split()[1:]
            for line in (proc / "status").read_text().splitlines()
            if line.startswith("Uid:")
        )
        if len(uids) != 4 or any(uid != str(os.getuid()) for uid in uids):
            raise ValueError("different owner")
        ticks = int((proc / "stat").read_text().rpartition(")")[2].split()[19])
        return ticks
    except (OSError, ValueError, IndexError, StopIteration) as exc:
        raise IdleError("Gamescope session is unavailable or changed") from exc


def compositor():
    pids = command(
        ["/usr/bin/pgrep", "-u", str(os.getuid()), "-f", r"^(/usr/bin/)?gamescope( |$)"]
    ).split()
    if len(pids) != 1 or not re.fullmatch(r"[0-9]{1,10}", pids[0]):
        raise IdleError("Cannot identify one Gamescope session")
    pid = int(pids[0])
    return pid, process_identity(pid)


def displays():
    # Gaming Mode's game display can be :1; Steam's counter lives on its own
    # root display (normally :0). Probe only existing local sockets, never a
    # hostname supplied through DISPLAY, and never create a missing property.
    try:
        candidates = set()
        for entry in Path("/tmp/.X11-unix").iterdir():
            if not entry.name.startswith("X"):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                candidates.add(":" + entry.name[1:])
    except OSError as exc:
        raise IdleError("Gamescope X11 sockets unavailable") from exc
    return sorted(value for value in candidates if DISPLAY_PATTERN.fullmatch(value))[:16]


def read_counter(display):
    output = command(
        ["/usr/bin/xprop", "-display", display, "-root", "-len", "64", "-f", ATOM, "32c", ATOM],
        display,
    )
    match = COUNTER_PATTERN.fullmatch(output)
    if not match or int(match[1]) > 0xFFFFFFFF:
        raise IdleError("Gamescope activity counter missing or incompatible")
    return int(match[1])


@dataclass(frozen=True)
class Target:
    display: str
    pid: int
    ticks: int

    def token(self):
        return json.dumps([self.display, self.pid, self.ticks], separators=(",", ":"))

    @classmethod
    def parse(cls, token):
        try:
            if len(token) > 128:
                raise ValueError("oversized token")
            value = json.loads(token)
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError("invalid target")
            display, pid, ticks = value
            if not isinstance(display, str) or not DISPLAY_PATTERN.fullmatch(display):
                raise ValueError("nonlocal display")
            if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
                raise ValueError("invalid pid")
            if type(ticks) is not int or not 0 <= ticks <= 0xFFFFFFFFFFFFFFFF:
                raise ValueError("invalid process start time")
            return cls(display, pid, ticks)
        except (ValueError, TypeError, RecursionError) as exc:
            raise IdleError("Invalid Gamescope idle target") from exc

    def pulse(self):
        if os.geteuid() == 0:
            raise IdleError("Gamescope activity must run as the desktop user")
        if process_identity(self.pid) != self.ticks:
            raise IdleError("Gamescope session changed")
        # Read the *current* counter each time, including real intervening input.
        value = (read_counter(self.display) + 1) & 0xFFFFFFFF
        command(
            [
                "/usr/bin/xprop",
                "-display",
                self.display,
                "-root",
                "-f",
                ATOM,
                "32c",
                "-set",
                ATOM,
                str(value),
            ],
            self.display,
        )


class IdleKeepalive:
    def __init__(self, target=None):
        self.target = target
        self.next_pulse = 0.0

    @classmethod
    def start(cls):
        if not gaming_mode():
            return cls()
        if os.environ.get(DISABLE_VARIABLE) == "1":
            print(
                "DeckThere: idle keepalive disabled; disable Steam automatic dim/sleep manually while sharing.",
                file=sys.stderr,
            )
            return cls()
        if os.geteuid() == 0:
            raise IdleError("Gamescope activity must run as the desktop user")
        pid, ticks = compositor()
        targets = []
        for display in displays():
            try:
                read_counter(display)
            except IdleError:
                continue
            targets.append(Target(display, pid, ticks))
        if len(targets) != 1:
            raise IdleError("Cannot identify one Steam Gamescope activity counter")
        instance = cls(targets[0])
        instance.tick()
        print(
            "DeckThere: Gamescope idle keepalive active; saved power settings unchanged.",
            file=sys.stderr,
        )
        return instance

    def tick(self, now=None):
        if self.target is None:
            return
        now = time.monotonic() if now is None else now
        if now < self.next_pulse:
            return
        self.target.pulse()
        self.next_pulse = now + INTERVAL


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    try:
        if arguments == ["start"]:
            idle = IdleKeepalive.start()
            if idle.target is not None:
                print(idle.target.token())
        elif len(arguments) == 2 and arguments[0] == "pulse":
            Target.parse(arguments[1]).pulse()
        else:
            raise IdleError("Usage: deckthere_idle.py start | pulse TARGET")
    except IdleError as exc:
        print(
            f"DeckThere idle protection failed: {exc}. See README: Gaming Mode idle handling.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
