#!/usr/bin/env python3
"""Record whether this checkout is eligible for published stable updates."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def metadata():
    branch = git("branch", "--show-current")
    tag = git("describe", "--tags", "--abbrev=0")
    exact = git("describe", "--tags", "--exact-match")
    clean = not git("status", "--porcelain")
    valid = bool(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag))
    stable = clean and valid and (branch == "main" or (not branch and exact == tag))
    return {
        "schema": 1,
        "version": tag if valid else "v0.0.0",
        "commit": git("rev-parse", "HEAD"),
        "channel": "stable" if stable else "development",
    }


if __name__ == "__main__":
    print(json.dumps(metadata()))
