from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QModelIndex, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
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
from mooring.models.roles import Role
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from mooring.ui.pages.common import page_title
from mooring.ui.widgets.delegates import BubbleDelegate, ConversationDelegate
from mooring.ui.widgets.new_message import NewMessageDialog


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
        #: Thread to select once it shows up in the list (from a notification).
        self._pending_thread: int | None = None
        #: Recipient of a "new message" whose thread doesn't exist yet.
        self._pending_addresses: tuple[str, ...] | None = None
        self._awaiting_attachment = False
        #: Returns (number, text) or None; replaced in tests.
        self.ask_new_message: Callable[[], tuple[str, str] | None] = self._ask_new_message

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
        self.new_button = QPushButton("New message")
        header = QHBoxLayout()
        header.addWidget(page_title("Messages"), 1)
        header.addWidget(self.new_button)
        lv.addLayout(header)
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
        self.new_button.clicked.connect(self._new_message)
        self.msg_view.clicked.connect(self._on_message_clicked)
        backend.attachmentReady.connect(self._on_attachment_ready)
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
        """The list model resets on every change; keep (or reach) the right thread selected."""
        if self._pending_addresses is not None:
            match = next(
                (
                    c
                    for c in (
                        self.conversations.conversation(r)
                        for r in range(self.conversations.rowCount())
                    )
                    if c is not None and c.addresses == self._pending_addresses
                ),
                None,
            )
            if match is not None:
                self._pending_addresses = None
                self._pending_thread = match.thread_id
        target = (
            self._pending_thread if self._pending_thread is not None else self.messages.thread_id
        )
        if target is None:
            return
        row = self.conversations.row_for_thread(target)
        if row >= 0:
            self._pending_thread = None
            self.conv_view.setCurrentIndex(self.conversations.index(row))

    @property
    def current_thread(self) -> int | None:
        return self.messages.thread_id

    def open_thread(self, thread_id: int) -> None:
        """Select a thread now, or as soon as it appears in the list."""
        self._pending_thread = thread_id
        self._restore_selection()

    def _new_message(self) -> None:
        if not self._device_id:
            return
        result = self.ask_new_message()
        if result is None:
            return
        number, text = result
        existing = next(
            (
                c
                for c in (
                    self.conversations.conversation(r) for r in range(self.conversations.rowCount())
                )
                if c is not None and c.addresses == (number,)
            ),
            None,
        )
        # Decide what to select *before* sending: a backend may answer synchronously.
        if existing:
            self.open_thread(existing.thread_id)
        else:
            self._pending_addresses = (number,)
        thread = existing.thread_id if existing else None
        self._backend.send_message(self._device_id, (number,), text, thread)

    def _ask_new_message(self) -> tuple[str, str] | None:
        dialog = NewMessageDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result_value()
        return None

    # --- attachments ---------------------------------------------------------

    def _on_message_clicked(self, index: QModelIndex) -> None:
        msg = index.data(Role.Item)
        att = next((a for a in msg.attachments if a.is_image), None) if msg else None
        if att is not None:
            self._awaiting_attachment = True
            self._backend.request_attachment(self._device_id, att)

    def _on_attachment_ready(self, device_id: str, path: str) -> None:
        if device_id == self._device_id and self._awaiting_attachment:
            self._awaiting_attachment = False
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

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
