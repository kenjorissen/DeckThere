#!/usr/bin/env python3
"""Manual stable-release checks and staging. Network and Qt run unprivileged."""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deckthere_launch_check as launch_check  # noqa: E402
from deckthere_update_common import (  # noqa: E402
    MAX_ARCHIVE,
    SCHEMA,
    UpdateError,
    members,
    relative,
    validate_bundle,
    version,
)

REPOSITORY = "kenjorissen/DeckThere"
API = f"https://api.github.com/repos/{REPOSITORY}"
INSTALLER = Path("/home/.deckthere/bin/deckthere_update_install.py")
EXIT_UPDATE = 42


def fetch(url, limit):
    request = urllib.request.Request(
        url, headers={"User-Agent": "DeckThere-Updater", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if not response.url.startswith("https://"):
            raise UpdateError("Update download redirected away from HTTPS")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("Update response exceeds size limit")
    return data


def api(path):
    return json.loads(fetch(API + path, 1024 * 1024))


def installed(base):
    try:
        data = json.loads((base / "update-info.json").read_text())
        version(data["version"])
        if data.get("channel") not in ("stable", "development"):
            raise ValueError("channel")
        return data
    except (OSError, ValueError, KeyError, UpdateError) as exc:
        raise UpdateError("Run setup once to enable in-app updates") from exc


def latest():
    try:
        release = api("/releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    tag = release["tag_name"]
    version(tag)
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdateError("GitHub did not return a published stable release")
    obj = api("/git/ref/tags/" + tag)["object"]
    for _ in range(4):
        sha = obj["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise UpdateError("Invalid release commit")
        if obj["type"] == "commit":
            return {
                "version": tag,
                "commit": sha,
                "notes": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
            }
        if obj["type"] != "tag":
            break
        obj = api("/git/tags/" + sha)["object"]
    raise UpdateError("Could not resolve the stable release")


def check(base):
    current = installed(base)
    if current["channel"] != "stable":
        return {
            "available": False,
            "message": "Development branch: update manually. In-app updates are for stable installations.",
        }
    release = latest()
    if release is None:
        return {"available": False, "message": "No published stable release is available yet."}
    available = version(release["version"]) > version(current["version"])
    return {
        "available": available,
        "release": release,
        "message": f"Installed {current['version']}. "
        + (
            f"{release['version']} is available."
            if available
            else "You’re up to date with stable releases."
        ),
    }


def owned_command(args, timeout):
    # Cancellation owns the whole download/validation process tree.
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=launch_check.environment(),
    )
    try:
        process.communicate(timeout=timeout)
        if process.returncode:
            raise UpdateError("Qt runtime preparation failed; try again in Desktop Mode")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def make_bundle(source, release, base, directory):
    unpacked = members(source)
    prefixes = {name.split("/", 1)[0] for name in unpacked}
    if len(prefixes) != 1:
        raise UpdateError("Unexpected source archive layout")
    prefix = prefixes.pop() + "/"
    source_files = {name[len(prefix) :]: item for name, item in unpacked.items()}
    try:
        manifest = json.loads(source_files["packaging/update-manifest.json"][0])
        if manifest["schema"] != SCHEMA:
            raise UpdateError("This release requires a newer installer; run setup manually")
        files = {}
        for destination, source_name in manifest["files"].items():
            relative(destination)
            relative(source_name)
            files[destination] = source_files[source_name]
        qt_tool = directory / "qt-helper.py"
        qt_tool.write_bytes(source_files["tools/deckthere-gui-deps.py"][0])
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("Release does not contain a supported update manifest") from exc
    try:
        owned_command(
            [sys.executable, "-I", str(qt_tool), "--check", "--destination", str(base / "pylib")],
            45,
        )
    except UpdateError:
        runtime = directory / "runtime"
        owned_command([sys.executable, "-I", str(qt_tool), "--destination", str(runtime)], 900)
        for path in runtime.rglob("*"):
            if path.is_symlink():
                raise UpdateError("Unexpected symlink in prepared Qt runtime")
            if path.is_file():
                files["user/pylib/" + path.relative_to(runtime).as_posix()] = (
                    path.read_bytes(),
                    0o755 if path.stat().st_mode & 0o111 else 0o644,
                )
    metadata = {
        "schema": SCHEMA,
        "version": release["version"],
        "commit": release["commit"],
        "channel": "stable",
    }
    files["meta.json"] = (json.dumps(metadata).encode(), 0o644)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, (data, mode) in files.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), mode
            archive.addfile(entry, io.BytesIO(data))
    data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    validate_bundle(data, digest)
    return data, digest


def prepare(base, tag, commit):
    result = check(base)
    release = result.get("release", {})
    if not result["available"] or release.get("version") != tag or release.get("commit") != commit:
        raise UpdateError("Release information changed. Check for updates again.")
    cache = base / ".updates"
    cache.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="release-", dir=cache))
    try:
        source = fetch(f"https://codeload.github.com/{REPOSITORY}/tar.gz/{commit}", MAX_ARCHIVE)
        data, digest = make_bundle(source, release, base, directory)
        payload = directory / "update.tar.gz"
        payload.write_bytes(data)
        request = {"archive": str(payload), "sha256": digest, "version": tag}
        temporary = directory / "request.json"
        temporary.write_text(json.dumps(request))
        os.replace(temporary, base / "update-request.json")
        return {
            "ready": True,
            "message": "Download verified. Stopping sharing to apply the update…",
        }
    except BaseException:
        shutil.rmtree(directory)
        raise


def apply_pending(base):
    """Called by the session supervisor only after normal sharing cleanup."""
    request_path = base / "update-request.json"
    directory = None
    try:
        request = json.loads(request_path.read_text())
        archive = Path(request["archive"])
        if archive.is_symlink() or not archive.resolve().is_relative_to(
            (base / ".updates").resolve()
        ):
            raise UpdateError("Invalid prepared update location")
        directory = archive.parent
        validate_bundle(archive.read_bytes(), request["sha256"])
        if not launch_check.trusted(INSTALLER):
            raise UpdateError("Trusted updater is missing. Run setup in Desktop Mode.")
        with launch_check.authentication_agent():
            # Match repair: start the native agent before a human confirmation,
            # allowing it to register before pkexec requests authentication.
            choice = launch_check.run(
                [
                    launch_check.DIALOG,
                    "--title",
                    "DeckThere update",
                    "--yes-label",
                    "Install",
                    "--no-label",
                    "Cancel",
                    "--yesno",
                    f"Install DeckThere {request['version']}?\n\n"
                    "Sharing has stopped. The system will request administrator "
                    "authorization to install the update.",
                ],
                timeout=120,
            )
            if choice != 0:
                return 126
            result = subprocess.run(
                [
                    "/usr/bin/pkexec",
                    "--disable-internal-agent",
                    str(INSTALLER),
                    str(archive),
                    request["sha256"],
                ],
                env=launch_check.environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        if result.returncode:
            print(
                f"Update installer exited {result.returncode}: {result.stderr[-4000:]}",
                file=sys.stderr,
            )
        if result.returncode in (126, 127):
            text = "Update was not authorized. Relaunch DeckThere to use the existing installation."
        elif result.returncode:
            text = "Update failed. The installer attempted to restore the previous files. Relaunch DeckThere or rerun setup in Desktop Mode."
        else:
            text = f"DeckThere {request['version']} installed. Launch DeckThere again from Steam."
        launch_check.run(
            [launch_check.DIALOG, "--title", "DeckThere update", "--msgbox", text], timeout=120
        )
        return result.returncode
    except (OSError, ValueError, KeyError, UpdateError, subprocess.TimeoutExpired) as exc:
        launch_check.message(
            f"Update could not finish: {exc}. Relaunch DeckThere or run setup in Desktop Mode."
        )
        return 1
    finally:
        request_path.unlink(missing_ok=True)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "prepare"))
    parser.add_argument("--base", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--tag")
    parser.add_argument("--commit")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run update checks as your normal user")

    def cancel(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, cancel)
    try:
        result = (
            check(args.base)
            if args.action == "check"
            else prepare(args.base, args.tag, args.commit)
        )
        print(json.dumps(result), flush=True)
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        UpdateError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(
            json.dumps(
                {
                    "error": str(exc)
                    if isinstance(exc, UpdateError)
                    else "Update check/download failed. Check your connection and try again.",
                    "detail": type(exc).__name__,
                }
            ),
            flush=True,
        )
        return 1
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())
