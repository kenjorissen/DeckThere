#!/usr/bin/python3 -I
"""Authenticated application update; never downloads or touches license/settings."""

import argparse
import fcntl
import json
import os
import pwd
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deckthere_repair as repair  # noqa: E402
from deckthere_update_common import MAX_ARCHIVE, UpdateError, validate_bundle, version  # noqa: E402

BIN = Path("/home/.deckthere/bin")


def read_bundle(path, uid):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size > MAX_ARCHIVE:
            raise UpdateError("Invalid prepared update file")
        return os.read(fd, MAX_ARCHIVE + 1)
    finally:
        os.close(fd)


def publish(files, after=lambda: None):
    """Stage everything first, then restore originals if any publication fails."""
    originals, staged, published = {}, {}, []
    try:
        for path, (data, mode) in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            for parent in [*path.parents, path]:
                if parent.is_symlink():
                    raise UpdateError("Refusing symlinked installation path")
            if path.exists():
                if not path.is_file():
                    raise UpdateError("Installation target is not a regular file")
                originals[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            else:
                originals[path] = None
            fd, name = tempfile.mkstemp(prefix=".update-", dir=path.parent)
            staged[path] = Path(name)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        for path, temporary in staged.items():
            os.replace(temporary, path)
            published.append(path)
        after()
    except BaseException:
        for path in reversed(published):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                fd, name = tempfile.mkstemp(prefix=".rollback-", dir=path.parent)
                with os.fdopen(fd, "wb") as stream:
                    os.fchmod(stream.fileno(), original[1])
                    stream.write(original[0])
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def install_user(base, files, metadata):
    ordinary = {
        base / name[5:]: item
        for name, item in files.items()
        if name.startswith("user/") and not name.startswith("user/pylib/")
    }
    ordinary[base / "update-info.json"] = (json.dumps(metadata).encode(), 0o644)
    runtime = {
        name[len("user/pylib/") :]: item
        for name, item in files.items()
        if name.startswith("user/pylib/")
    }
    if not runtime:
        publish(ordinary)
        return
    with tempfile.TemporaryDirectory(prefix=".update-runtime-", dir=base) as directory:
        stage, backup = Path(directory) / "new", Path(directory) / "previous"
        stage.mkdir()
        publish({stage / name: item for name, item in runtime.items()})
        destination = base / "pylib"
        if destination.is_symlink():
            raise UpdateError("Refusing symlinked Qt runtime")

        def replace_runtime():
            if destination.exists():
                os.replace(destination, backup)
            try:
                os.replace(stage, destination)
            except BaseException:
                if backup.exists():
                    os.replace(backup, destination)
                raise

        publish(ordinary, replace_runtime)


def child_install(owner, files, metadata, lock):
    pid = os.fork()
    if pid == 0:
        try:
            os.close(lock)
            os.setgroups([])
            os.setgid(owner.pw_gid)
            os.setuid(owner.pw_uid)
            os.environ.clear()
            os.environ.update(repair.ENV, HOME=owner.pw_dir)
            install_user(Path(owner.pw_dir) / ".local/share/deckthere", files, metadata)
            os._exit(0)
        except BaseException as exc:
            print(f"Could not install user files: {exc}", file=sys.stderr, flush=True)
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    if status != 0:
        raise UpdateError("User-file update failed; system files rolled back")


def apply(path, digest):
    if os.geteuid() != 0:
        raise UpdateError("Administrator authorization is required")
    uid = int(repair.read_file(BIN / "owner-uid")[0])
    if os.environ.get("PKEXEC_UID") != str(uid) or uid == 0:
        raise UpdateError("Only the installation owner can apply updates")
    owner = pwd.getpwuid(uid)
    current = json.loads(repair.read_file(BIN / "update-info.json")[0])
    metadata, files = validate_bundle(read_bundle(path, uid), digest)
    if current.get("channel") != "stable" or version(metadata["version"]) <= version(
        current["version"]
    ):
        raise UpdateError("Only newer stable releases can be installed")
    repair.parents(repair.LOCK.parent)
    repair.LOCK.parent.mkdir(mode=0o700, exist_ok=True)
    repair.safe_stat(repair.LOCK.parent, directory=True)
    fd = os.open(repair.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        repair.safe_stat(repair.LOCK)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if repair.service()["ActiveState"] not in ("inactive", "failed"):
            raise UpdateError("Sharing is still active; update cancelled")
        targets = {BIN / name[5:]: item for name, item in files.items() if name.startswith("root/")}
        targets[BIN / "update-info.json"] = (json.dumps(metadata).encode(), 0o644)
        build_info = repair.read_file(BIN / "build-info.txt")[0].decode()
        retained = [
            line
            for line in build_info.splitlines()
            if not line.startswith(("DECKTHERE_COMMIT=", "INSTALLED_UTC="))
        ]
        retained += [
            f"DECKTHERE_COMMIT={metadata['commit']}",
            f"INSTALLED_UTC={datetime.now(timezone.utc).isoformat()}",
        ]
        targets[BIN / "build-info.txt"] = (("\n".join(retained) + "\n").encode(), 0o644)
        targets[repair.UNIT] = files["root/deckthere.service"]
        for target in targets:
            repair.parents(target)
            if target.exists() or target.is_symlink():
                repair.safe_stat(target)

        def finish():
            if repair.command(["/usr/bin/systemctl", "--system", "daemon-reload"]).returncode:
                raise UpdateError("Could not reload updated service")
            child_install(owner, files, metadata, fd)

        try:
            with repair.defer_termination():
                publish(targets, finish)
        except BaseException:
            repair.command(["/usr/bin/systemctl", "--system", "daemon-reload"])
            raise
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("sha256")
    args = parser.parse_args()
    try:
        apply(args.archive, args.sha256)
        return 0
    except (OSError, ValueError, KeyError, UpdateError, repair.RepairError) as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
