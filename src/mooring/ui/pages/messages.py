from __future__ import annotations

from PyQt6.QtCore import QModelIndex, Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mooring.backend.base import PhoneBackend
from mooring.models import ConversationListModel, MessageListModel
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from mooring.ui.pages.common import page_title
from mooring.ui.widgets.delegates import BubbleDelegate, ConversationDelegate


class MessagesPage(QWidget):
    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings = backend, settings
        self._device_id = ""
        self._keep_from_bottom: int | None = None

        self.conversations = ConversationListModel(backend, self)
        self.messages = MessageListModel(backend, parent=self)

        # Left: conversation list
        self.conv_view = QListView()
        self.conv_view.setModel(self.conversations)
        self.conv_view.setItemDelegate(ConversationDelegate(theme, self.conv_view))
        self.conv_view.setMouseTracking(True)
        self.conv_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 24, 8, 8)
        lv.addWidget(page_title("Messages"))
        lv.addWidget(self.conv_view, 1)

        # Right: thread
        self.thread_title = QLabel("Select a conversation")
        self.thread_title.setObjectName("threadTitle")
        self.msg_view = QListView()
        self.msg_view.setModel(self.messages)
        self.msg_view.setItemDelegate(BubbleDelegate(theme, self.msg_view))
        self.msg_view.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.msg_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.msg_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.msg_view.setUniformItemSizes(False)
        self.msg_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.compose = QLineEdit()
        self.compose.setPlaceholderText("Type a message")
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        compose_row = QHBoxLayout()
        compose_row.addWidget(self.compose, 1)
        compose_row.addWidget(self.send_button)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 28, 20, 16)
        rv.addWidget(self.thread_title)
        rv.addWidget(self.msg_view, 1)
        rv.addLayout(compose_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 600])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        # Wiring
        sel = self.conv_view.selectionModel()
        assert sel is not None
        sel.currentChanged.connect(self._on_current_changed)
        self.conversations.modelReset.connect(self._restore_selection)
        self.messages.modelReset.connect(self._scroll_to_bottom_later)
        self.messages.rowsInserted.connect(self._on_rows_inserted)
        self.messages.olderLoaded.connect(self._on_older_loaded)
        bar = self.msg_view.verticalScrollBar()
        assert bar is not None
        bar.valueChanged.connect(self._on_scrolled)
        self.compose.returnPressed.connect(self._send)
        self.send_button.clicked.connect(self._send)
        settings.activeDeviceChanged.connect(self.set_device)
        theme.changed.connect(self._repaint)
        backend.devicesChanged.connect(self._on_devices_changed)

        self._set_compose_enabled(False)
        self.set_device(settings.active_device)

    def _on_devices_changed(self) -> None:
        self.set_device(self._settings.active_device)

    # --- device / selection --------------------------------------------------

    def set_device(self, device_id: str) -> None:
        if device_id == self._device_id:
            return
        self._device_id = device_id
        self.messages.open_thread(device_id, None)
        self.thread_title.setText("Select a conversation")
        self._set_compose_enabled(False)
        self.conversations.set_device(device_id)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        conv = self.conversations.conversation(current.row())
        if conv is None or conv.thread_id == self.messages.thread_id:
            return
        self.thread_title.setText(conv.display_name)
        self._set_compose_enabled(True)
        self.messages.open_thread(self._device_id, conv.thread_id)
        self.compose.setFocus()

    def _restore_selection(self) -> None:
        """The list model resets on every change; keep the open thread selected."""
        if self.messages.thread_id is None:
            return
        row = self.conversations.row_for_thread(self.messages.thread_id)
        if row >= 0:
            self.conv_view.setCurrentIndex(self.conversations.index(row))

    # --- sending -------------------------------------------------------------

    def _set_compose_enabled(self, enabled: bool) -> None:
        self.compose.setEnabled(enabled)
        self.send_button.setEnabled(enabled)

    def _send(self) -> None:
        text = self.compose.text().strip()
        conv = self.conversations.conversation(
            self.conversations.row_for_thread(self.messages.thread_id or -1)
        )
        if not text or conv is None:
            return
        self._backend.send_message(self._device_id, conv.addresses, text, conv.thread_id)
        self.compose.clear()

    # --- scrolling & lazy loading -------------------------------------------

    def _scroll_to_bottom_later(self) -> None:
        QTimer.singleShot(0, self.msg_view.scrollToBottom)

    def _on_rows_inserted(self, _parent: QModelIndex, first: int, _last: int) -> None:
        if first > 0:  # appended (new or sent message)
            self._scroll_to_bottom_later()

    def _on_scrolled(self, value: int) -> None:
        bar = self.msg_view.verticalScrollBar()
        if bar is None or value != bar.minimum() or bar.maximum() == 0:
            return
        if self.messages.load_older():
            self._keep_from_bottom = bar.maximum() - value

    def _on_older_loaded(self, _count: int) -> None:
        keep = self._keep_from_bottom
        self._keep_from_bottom = None
        if keep is None:
            return

        def restore() -> None:
            self.msg_view.doItemsLayout()  # type: ignore[attr-defined]  # missing from stubs
            bar = self.msg_view.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum() - keep)

        QTimer.singleShot(0, restore)

    def _repaint(self) -> None:
        for view in (self.conv_view, self.msg_view):
            if vp := view.viewport():
                vp.update()
