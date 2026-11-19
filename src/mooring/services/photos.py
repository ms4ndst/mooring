"""Find the newest photos on the mounted phone storage, off the GUI thread.

Phones disagree on folder names (and some keep no photos locally at all), so we
scan the usual camera/screenshot folders first, then everything else under DCIM/
and Pictures/, skipping hidden folders such as ``.thumbnails``.

Never log file names: they can contain dates, places or people's names.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
#: Searched in this order; duplicates are skipped.
PHOTO_DIRS = ("DCIM/Camera", "DCIM/Screenshots", "Pictures/Screenshots", "DCIM", "Pictures")
MAX_DEPTH = 3
DEFAULT_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Photo:
    path: str  # local path inside the sshfs mount
    name: str
    mtime: float
    size: int


def scan_photos(root: str | Path, limit: int = DEFAULT_LIMIT) -> list[Photo]:
    """Newest-first image files under the standard photo folders of ``root``."""
    root = Path(root)
    found: dict[str, Photo] = {}
    for rel in PHOTO_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        base_depth = len(base.parts)
        for dirpath, dirnames, filenames in os.walk(base):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = (
                [] if depth >= MAX_DEPTH else [d for d in dirnames if not d.startswith(".")]
            )
            for name in filenames:
                if name.startswith(".") or Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                full = os.path.join(dirpath, name)
                if full in found:
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                found[full] = Photo(full, name, st.st_mtime, st.st_size)
    photos = sorted(found.values(), key=lambda p: p.mtime, reverse=True)[:limit]
    log.info("photos: %d found (showing %d)", len(found), len(photos))
    return photos


class _ScanSignals(QObject):
    """Owned by the job (no Qt parent), so a deleted scanner can't be emitted into."""

    done = pyqtSignal(str, list)  # device id, list[Photo]
    failed = pyqtSignal(str)


class _ScanJob(QRunnable):
    def __init__(self, device_id: str, root: str, limit: int) -> None:
        super().__init__()
        self._device_id, self._root, self._limit = device_id, root, limit
        self.signals = _ScanSignals()

    def run(self) -> None:
        try:
            self.signals.done.emit(self._device_id, scan_photos(self._root, self._limit))
        except OSError as exc:
            log.warning("photo scan failed: %s", type(exc).__name__)
            self.signals.failed.emit(self._device_id)


class PhotoScanner(QObject):
    """Runs ``scan_photos`` on the global thread pool; results arrive on the GUI thread."""

    scanned = pyqtSignal(str, list)  # device id, list[Photo]
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: set[_ScanJob] = set()

    def scan(self, device_id: str, root: str, limit: int = DEFAULT_LIMIT) -> None:
        job = _ScanJob(device_id, root, limit)
        self._jobs.add(job)  # keep the Python object alive until it reports back

        def finished(*_args: object) -> None:
            self._jobs.discard(job)

        job.signals.done.connect(self.scanned)
        job.signals.failed.connect(self.failed)
        job.signals.done.connect(finished)
        job.signals.failed.connect(finished)
        pool = QThreadPool.globalInstance()
        assert pool is not None
        pool.start(job)
