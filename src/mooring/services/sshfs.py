"""Recover from a dead sshfs mount left behind by kdeconnectd.

When the phone changes network or kdeconnectd restarts while the storage is mounted, the
FUSE mount can outlive its sshfs process. Every access then fails with EIO / ENOTCONN, and
kdeconnectd's next mount on the same mount point fails ("sshfs finished with exit code 1").
Seen live on 2026-11-19.

``clear_stale_mount`` lazily unmounts such a mount, but only if it is a fuse.sshfs mount
under the user's own runtime dir (/run/user/<uid>/) that actually fails when accessed.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

STALE_ERRNOS = {errno.EIO, errno.ENOTCONN, errno.ESTALE}
STAT_TIMEOUT = 3.0


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def _unescape(field: str) -> str:
    """/proc/mounts escapes spaces and friends as octal (\\040)."""
    return field.encode().decode("unicode_escape")


def is_sshfs_mount(path: str, mounts_text: str | None = None) -> bool:
    if mounts_text is None:
        try:
            mounts_text = Path("/proc/self/mounts").read_text()
        except OSError:
            return False
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and _unescape(parts[1]) == path and parts[2] == "fuse.sshfs":
            return True
    return False


def is_dead(path: str, timeout: float = STAT_TIMEOUT) -> bool:
    """True if accessing ``path`` fails like a dead FUSE mount (or hangs)."""
    result: list[BaseException | None] = []

    def probe() -> None:
        try:
            os.stat(path)
            os.listdir(path)
            result.append(None)
        except OSError as exc:
            result.append(exc)

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return True  # hung: as unusable as a dead mount
    err = result[0] if result else None
    return isinstance(err, OSError) and err.errno in STALE_ERRNOS


def clear_stale_mount(path: str) -> bool:
    """Lazily unmount a dead sshfs mount at ``path``. Returns True if one was removed."""
    resolved = os.path.realpath(path)
    if not resolved.startswith(str(runtime_dir()) + os.sep):
        return False
    if not is_sshfs_mount(resolved) or not is_dead(resolved):
        return False
    tool = shutil.which("fusermount3") or shutil.which("fusermount")
    if tool is None:
        log.warning("dead sshfs mount found but fusermount is missing")
        return False
    done = subprocess.run([tool, "-u", "-z", resolved], capture_output=True, timeout=10)
    if done.returncode != 0:
        log.warning("couldn't unmount dead sshfs mount (exit %d)", done.returncode)
        return False
    log.info("removed a dead sshfs mount of the phone's storage")
    return True
