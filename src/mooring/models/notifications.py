from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from mooring.backend.base import Notification, PhoneBackend
from mooring.models.roles import Role

_Index = QModelIndex | QPersistentModelIndex


class NotificationListModel(QAbstractListModel):
    """Active phone notifications of the active device, newest first."""

    def __init__(self, backend: PhoneBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._device_id = ""
        self._items: list[Notification] = []
        backend.notificationPosted.connect(self._on_posted)
        backend.notificationRemoved.connect(self._on_removed)

    def set_device(self, device_id: str) -> None:
        self._device_id = device_id
        items = self._backend.notifications(device_id) if device_id else []
        items.sort(key=lambda n: n.timestamp, reverse=True)
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def notification(self, row: int) -> Notification | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def _on_posted(self, device_id: str, notification: Notification) -> None:
        if device_id != self._device_id:
            return
        row = next((i for i, n in enumerate(self._items) if n.id == notification.id), -1)
        if row >= 0:  # updated: replace in place, keep its position
            self._items[row] = notification
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)
            return
        # newest first
        row = next(
            (i for i, n in enumerate(self._items) if n.timestamp < notification.timestamp),
            len(self._items),
        )
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.insert(row, notification)
        self.endInsertRows()

    def _on_removed(self, device_id: str, notification_id: str) -> None:
        if device_id != self._device_id:
            return
        row = next((i for i, n in enumerate(self._items) if n.id == notification_id), -1)
        if row >= 0:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._items[row]
            self.endRemoveRows()

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        n = self._items[index.row()]
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return n.text
            case Role.Item:
                return n
            case Role.AppName:
                return n.app_name
            case Role.Title:
                return n.title
            case Role.Timestamp:
                return n.timestamp
        return None
