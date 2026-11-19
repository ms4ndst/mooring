from __future__ import annotations

from typing import Any

from PyQt6.QtCore import (
    QAbstractListModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QUrl,
)
from PyQt6.QtGui import QPixmap, QPixmapCache

from mooring.models.roles import Role
from mooring.services.photos import Photo
from mooring.services.thumbnails import ThumbnailCache

_Index = QModelIndex | QPersistentModelIndex


class PhotoListModel(QAbstractListModel):
    """Newest-first photos of one device; thumbnails fill in as they're generated."""

    def __init__(self, thumbs: ThumbnailCache, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thumbs = thumbs
        self._device_id = ""
        self._items: list[Photo] = []
        self._row_of_key: dict[str, int] = {}
        thumbs.ready.connect(self._on_thumb)

    def set_photos(self, device_id: str, photos: list[Photo]) -> None:
        self.beginResetModel()
        self._device_id, self._items, self._row_of_key = device_id, list(photos), {}
        self.endResetModel()

    def photo(self, row: int) -> Photo | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        return base | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else base

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        photo = self._items[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            return self._pixmap(index.row(), photo)
        if role == Qt.ItemDataRole.ToolTipRole:
            return photo.name
        if role == Role.Item:
            return photo
        if role == Role.Timestamp:
            return photo.mtime
        return None

    def _pixmap(self, row: int, photo: Photo) -> QPixmap | None:
        key, path = self._thumbs.path_for(self._device_id, photo)
        self._row_of_key[key] = row
        if path is None:
            return None
        pix = QPixmapCache.find(key)
        if pix is None:
            pix = QPixmap(path)
            if pix.isNull():
                return None
            QPixmapCache.insert(key, pix)
        return pix

    def _on_thumb(self, key: str, _path: str) -> None:
        row = self._row_of_key.get(key)
        if row is not None and row < len(self._items):
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    # --- drag out ------------------------------------------------------------

    def mimeTypes(self) -> list[str]:
        return ["text/uri-list"]

    def mimeData(self, indexes: Any) -> QMimeData:
        mime = QMimeData()
        rows = sorted({i.row() for i in indexes if i.isValid()})
        mime.setUrls([QUrl.fromLocalFile(self._items[r].path) for r in rows])
        return mime

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.CopyAction
