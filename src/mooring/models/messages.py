from __future__ import annotations

from typing import Any

from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    pyqtSignal,
)

from mooring.backend.base import Message, PhoneBackend
from mooring.models.roles import Role

_Index = QModelIndex | QPersistentModelIndex


class MessageListModel(QAbstractListModel):
    """Messages of one open thread, oldest first, loaded lazily in pages."""

    #: Older messages were inserted at the top. Arg: number of rows.
    olderLoaded = pyqtSignal(int)

    def __init__(
        self, backend: PhoneBackend, page_size: int = 50, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._page = page_size
        self._device_id = ""
        self._thread_id: int | None = None
        self._items: list[Message] = []
        self._ids: set[int] = set()
        self.loading = False
        self.exhausted = False
        backend.messagesLoaded.connect(self._on_loaded)
        backend.messageAdded.connect(self._on_added)

    @property
    def thread_id(self) -> int | None:
        return self._thread_id

    def open_thread(self, device_id: str, thread_id: int | None) -> None:
        self.beginResetModel()
        self._device_id, self._thread_id = device_id, thread_id
        self._items, self._ids = [], set()
        self.exhausted = thread_id is None
        self.loading = thread_id is not None
        self.endResetModel()
        if thread_id is not None:
            self._backend.request_conversation(device_id, thread_id, 0, self._page)

    def load_older(self) -> bool:
        """Request the next page of older messages. Returns False if nothing to do."""
        if self.loading or self.exhausted or self._thread_id is None:
            return False
        self.loading = True
        start = len(self._items)
        self._backend.request_conversation(
            self._device_id, self._thread_id, start, start + self._page
        )
        return True

    def _matches(self, device_id: str, thread_id: int) -> bool:
        return device_id == self._device_id and thread_id == self._thread_id

    def _on_loaded(self, device_id: str, thread_id: int, start: int, messages: list) -> None:
        if not self._matches(device_id, thread_id):
            return
        self.loading = False
        self.exhausted = len(messages) < self._page
        fresh = [m for m in messages if m.id not in self._ids]
        if start == 0:
            self.beginResetModel()
            self._items = list(fresh)
            self._ids = {m.id for m in fresh}
            self.endResetModel()
        elif fresh:
            self.beginInsertRows(QModelIndex(), 0, len(fresh) - 1)
            self._items[0:0] = fresh
            self._ids.update(m.id for m in fresh)
            self.endInsertRows()
            self.olderLoaded.emit(len(fresh))

    def _on_added(self, device_id: str, message: Message) -> None:
        if not self._matches(device_id, message.thread_id) or message.id in self._ids:
            return
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(message)
        self._ids.add(message.id)
        self.endInsertRows()

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        m = self._items[index.row()]
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return m.body
            case Role.Item:
                return m
            case Role.Timestamp:
                return m.timestamp
            case Role.Outgoing:
                return m.outgoing
        return None
