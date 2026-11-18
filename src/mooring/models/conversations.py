from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from mooring.backend.base import Conversation, PhoneBackend
from mooring.models.roles import Role

_Index = QModelIndex | QPersistentModelIndex


class ConversationListModel(QAbstractListModel):
    """Conversations of the active device, most recent first."""

    def __init__(self, backend: PhoneBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._device_id = ""
        self._items: list[Conversation] = []
        backend.conversationsChanged.connect(self._on_changed)

    def set_device(self, device_id: str) -> None:
        self._device_id = device_id
        self.reload()

    def reload(self) -> None:
        items = self._backend.conversations(self._device_id) if self._device_id else []
        items.sort(key=lambda c: c.last_message.timestamp, reverse=True)
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def _on_changed(self, device_id: str) -> None:
        if device_id == self._device_id:
            self.reload()

    def conversation(self, row: int) -> Conversation | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_for_thread(self, thread_id: int) -> int:
        return next((i for i, c in enumerate(self._items) if c.thread_id == thread_id), -1)

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        c = self._items[index.row()]
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return c.display_name
            case Role.Item:
                return c
            case Role.ThreadId:
                return c.thread_id
            case Role.Snippet:
                return c.last_message.body
            case Role.Timestamp:
                return c.last_message.timestamp
            case Role.Unread:
                return c.unread
            case Role.Outgoing:
                return c.last_message.outgoing
        return None
