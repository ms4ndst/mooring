"""Photo thumbnails, generated with Pillow in background threads and cached on disk.

Cache: ``~/.cache/mooring/thumbs`` (0700, files 0600). The key hashes the device,
path, size and mtime, so an edited photo gets a fresh thumbnail and file names
never appear in the cache directory.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import deque
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from mooring.services.paths import cache_root, private_dir
from mooring.services.photos import Photo

log = logging.getLogger(__name__)

SIZE = 256
#: Parallel full-size reads over sshfs; more just fights for the same link.
WORKERS = 3


def thumb_dir() -> Path:
    return cache_root() / "thumbs"


def thumb_key(device_id: str, photo: Photo) -> str:
    raw = f"{device_id}\0{photo.path}\0{photo.size}\0{photo.mtime}".encode()
    return hashlib.sha256(raw).hexdigest()[:40]


def make_thumbnail(source: str, target: Path, size: int = SIZE) -> bool:
    """Write a JPEG thumbnail of ``source`` to ``target``. False if unreadable."""
    try:
        with Image.open(source) as original:
            img: Image.Image = ImageOps.exif_transpose(original)
            img.thumbnail((size, size))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            tmp = target.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                img.save(fh, "JPEG", quality=85)
            tmp.replace(target)
        return True
    except (OSError, UnidentifiedImageError, ValueError) as exc:  # HEIC without plugin, etc.
        log.debug("thumbnail failed: %s", type(exc).__name__)
        return False


class _Signals(QObject):
    """Owned by its job (no Qt parent): never emits into an already-deleted object."""

    done = pyqtSignal(str, str)  # key, thumbnail path ("" on failure)


class _Job(QRunnable):
    def __init__(self, key: str, source: str, target: Path) -> None:
        super().__init__()
        self._key, self._source, self._target = key, source, target
        self.signals = _Signals()

    def run(self) -> None:
        ok = make_thumbnail(self._source, self._target)
        self.signals.done.emit(self._key, str(self._target) if ok else "")


class ThumbnailCache(QObject):
    """``path_for`` answers from disk at once, or queues a job and emits ``ready`` later.

    Jobs run on the application-wide thread pool, at most WORKERS at a time. A
    per-object QThreadPool would block in its C++ destructor while holding the GIL,
    deadlocking against Pillow threads that need the GIL to finish. Jobs are
    auto-deleted by the pool (PyQt keeps them alive until run() returns) and own
    their signal object, so deleting this cache mid-job is safe.
    """

    ready = pyqtSignal(str, str)  # key, thumbnail path ("" = couldn't make one)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._queue: deque[_Job] = deque()
        self._running: dict[str, _Job] = {}
        self._queued: set[str] = set()
        self._failed: set[str] = set()

    def path_for(self, device_id: str, photo: Photo) -> tuple[str, str | None]:
        """(key, cached thumbnail path or None). Queues generation when missing."""
        key = thumb_key(device_id, photo)
        target = thumb_dir() / f"{key}.jpg"
        if target.exists():
            return key, str(target)
        if key not in self._queued and key not in self._failed:
            private_dir(thumb_dir())
            self._queued.add(key)
            self._queue.append(_Job(key, photo.path, target))
            self._pump()
        return key, None

    def _pump(self) -> None:
        assert self._pool is not None
        while self._queue and len(self._running) < WORKERS:
            job = self._queue.popleft()
            job.signals.done.connect(self._on_done)
            self._running[job._key] = job
            self._pool.start(job)

    def failed(self, key: str) -> bool:
        return key in self._failed

    def forget_failures(self) -> None:
        self._failed.clear()

    def cancel_pending(self) -> None:
        """Drop queued (not yet running) jobs, e.g. when the photo list changes."""
        for job in self._queue:
            self._queued.discard(job._key)
        self._queue.clear()

    def _on_done(self, key: str, path: str) -> None:
        self._running.pop(key, None)
        self._queued.discard(key)
        if not path:
            self._failed.add(key)
        self.ready.emit(key, path)
        self._pump()

    def idle(self) -> bool:
        return not self._queue and not self._running
