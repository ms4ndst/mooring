"""Photos: newest pictures from the phone, with Open, Save to Pictures and drag-out."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QSize, QStandardPaths, QThreadPool, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QShowEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mooring.backend.base import PhoneBackend
from mooring.models.photos import PhotoListModel
from mooring.services.photos import Photo, PhotoScanner
from mooring.services.settings import SettingsStore
from mooring.services.thumbnails import ThumbnailCache
from mooring.theme.catppuccin import Theme
from mooring.ui.pages.common import page_title
from mooring.ui.widgets.delegates import PhotoDelegate

log = logging.getLogger(__name__)


def pictures_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return Path(location or Path.home() / "Pictures")


def unique_target(directory: Path, name: str) -> Path:
    target = directory / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        target = directory / f"{stem} ({n}){suffix}"
        n += 1
    return target


class _CopySignals(QObject):
    done = pyqtSignal(int, int)  # copied, failed


class _CopyJob(QRunnable):
    def __init__(self, photos: list[Photo], target: Path) -> None:
        super().__init__()
        self._photos, self._target = photos, target
        self.signals = _CopySignals()  # no Qt parent: outlives the page if needed

    def run(self) -> None:
        copied = failed = 0
        self._target.mkdir(parents=True, exist_ok=True)
        for p in self._photos:
            try:
                shutil.copy2(p.path, unique_target(self._target, p.name))
                copied += 1
            except OSError:
                failed += 1
        self.signals.done.emit(copied, failed)


class PhotosPage(QWidget):
    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings = backend, settings
        self._busy = False
        self.thumbs = ThumbnailCache(self)
        self.scanner = PhotoScanner(self)
        self.model = PhotoListModel(self.thumbs, self)
        #: Where "Save to Pictures" copies to; replaced in tests.
        self.save_dir: Callable[[], Path] = pictures_dir
        self._copy_jobs: set[_CopyJob] = set()

        self.status = QLabel()
        self.status.setObjectName("muted")
        self.refresh_button = QPushButton("Refresh")
        self.open_button = QPushButton("Open")
        self.save_button = QPushButton("Save to Pictures")
        self.save_button.setObjectName("primary")

        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(PhotoDelegate(theme, self.view))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self.view.setIconSize(QSize(PhotoDelegate.TILE, PhotoDelegate.TILE))
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setDragEnabled(True)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.view.setMouseTracking(True)

        header = QHBoxLayout()
        header.addWidget(page_title("Photos"))
        header.addWidget(self.status, 1)
        for b in (self.refresh_button, self.open_button, self.save_button):
            header.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.addLayout(header)
        layout.addWidget(self.view, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.open_button.clicked.connect(self._open_selected)
        self.save_button.clicked.connect(self._save_selected)
        self.view.doubleClicked.connect(lambda _i: self._open_selected())
        sel = self.view.selectionModel()
        assert sel is not None
        sel.selectionChanged.connect(self._update_buttons)
        self.model.modelReset.connect(self._update_buttons)
        backend.storageReady.connect(self._on_storage_ready)
        backend.storageFailed.connect(self._on_storage_failed)
        self.scanner.scanned.connect(self._on_scanned)
        self.scanner.failed.connect(self._on_scan_failed)
        settings.activeDeviceChanged.connect(self._on_device_changed)
        theme.changed.connect(self._repaint)
        self._update_buttons()

    # --- loading -------------------------------------------------------------

    def showEvent(self, event: QShowEvent | None) -> None:
        super().showEvent(event)
        if self.model.rowCount() == 0 and not self._busy:
            self.refresh()

    def _on_device_changed(self, _device_id: str) -> None:
        self.model.set_photos("", [])
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        device_id = self._settings.active_device
        device = self._backend.device(device_id)
        if device is None or not device.reachable or not device.trusted:
            self.status.setText("Phone not connected")
            return
        self._busy = True
        self.refresh_button.setEnabled(False)
        self.thumbs.forget_failures()
        # Always ask for the mount: cheap when it's up, and it repairs a dropped mount.
        self.status.setText("Opening phone storage…")
        self._backend.mount_storage(device_id)

    def _on_storage_ready(self, device_id: str, root: str) -> None:
        if device_id == self._settings.active_device and self._busy:
            self._scan(device_id, root)

    def _on_storage_failed(self, device_id: str, reason: str) -> None:
        if device_id == self._settings.active_device and self._busy:
            self._done()
            self.status.setText(reason)

    def _scan(self, device_id: str, root: str) -> None:
        self.status.setText("Looking for photos…")
        self.scanner.scan(device_id, root)

    def _on_scanned(self, device_id: str, photos: list[Photo]) -> None:
        if device_id != self._settings.active_device:
            return
        self._done()
        self.thumbs.cancel_pending()
        self.model.set_photos(device_id, photos)
        self.status.setText(f"{len(photos)} newest" if photos else "No photos on the phone yet")

    def _on_scan_failed(self, _device_id: str) -> None:
        self._done()
        self.status.setText("Couldn't read the phone's storage")

    def _done(self) -> None:
        self._busy = False
        self.refresh_button.setEnabled(True)

    # --- actions -------------------------------------------------------------

    def selected_photos(self) -> list[Photo]:
        sel = self.view.selectionModel()
        rows = sorted(i.row() for i in sel.selectedIndexes()) if sel else []
        return [p for p in (self.model.photo(r) for r in rows) if p is not None]

    def _update_buttons(self) -> None:
        has = bool(self.selected_photos())
        self.open_button.setEnabled(has)
        self.save_button.setEnabled(has)

    def _open_selected(self) -> None:
        for p in self.selected_photos()[:5]:  # don't spawn a viewer per photo for big selections
            QDesktopServices.openUrl(QUrl.fromLocalFile(p.path))

    def _save_selected(self) -> None:
        photos = self.selected_photos()
        if not photos:
            return
        self.save_button.setEnabled(False)
        self.status.setText(f"Saving {len(photos)}…")
        job = _CopyJob(photos, self.save_dir())
        self._copy_jobs.add(job)
        job.signals.done.connect(self._on_copied)
        job.signals.done.connect(lambda *_: self._copy_jobs.discard(job))
        pool = QThreadPool.globalInstance()
        assert pool is not None
        pool.start(job)

    def _on_copied(self, copied: int, failed: int) -> None:
        self._update_buttons()
        target = str(self.save_dir()).replace(str(Path.home()), "~")
        msg = f"Saved {copied} to {target}"
        if failed:
            msg += f" ({failed} failed)"
        self.status.setText(msg)

    def _repaint(self) -> None:
        if vp := self.view.viewport():
            vp.update()
