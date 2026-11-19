"""Phone notifications: newest first, dismiss one or all, reply to chat notifications."""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mooring.backend.base import Notification, PhoneBackend
from mooring.models import NotificationListModel
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from mooring.ui.pages.common import page_title
from mooring.ui.widgets.delegates import NotificationDelegate

EMPTY_TEXT = (
    "No notifications from the phone.\n\n"
    "If they never show up: in KDE Connect on the phone, open this computer → "
    "Plugin settings, turn on “Notification sync”, and allow notification access."
)


class NotificationsPage(QWidget):
    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings = backend, settings
        self.model = NotificationListModel(backend, self)

        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(NotificationDelegate(theme, self.view))
        self.view.setMouseTracking(True)
        self.view.setSpacing(2)
        self.empty = QLabel(EMPTY_TEXT)
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.dismiss = QPushButton("Dismiss")
        self.clear_all = QPushButton("Clear all")

        self.reply_edit = QLineEdit()
        self.reply_send = QPushButton("Reply")
        self.reply_send.setObjectName("primary")
        self.reply_row = QWidget()
        rr = QHBoxLayout(self.reply_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(self.reply_edit, 1)
        rr.addWidget(self.reply_send)

        header = QHBoxLayout()
        header.addWidget(page_title("Notifications"))
        header.addWidget(self.count, 1)
        header.addWidget(self.dismiss)
        header.addWidget(self.clear_all)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.addLayout(header)
        layout.addWidget(self.empty)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.reply_row)

        self.dismiss.clicked.connect(self._dismiss_selected)
        self.clear_all.clicked.connect(self._dismiss_all)
        self.reply_send.clicked.connect(self._send_reply)
        self.reply_edit.returnPressed.connect(self._send_reply)
        self.reply_edit.textChanged.connect(lambda _t: self._update_buttons())
        sel = self.view.selectionModel()
        assert sel is not None
        sel.currentChanged.connect(self._on_current_changed)
        for signal in (
            self.model.modelReset,
            self.model.rowsInserted,
            self.model.rowsRemoved,
            self.model.dataChanged,
        ):
            signal.connect(self._update_buttons)
        settings.activeDeviceChanged.connect(self.model.set_device)
        backend.devicesChanged.connect(self._reload)
        theme.changed.connect(self._repaint)
        self._reload()

    def _reload(self) -> None:
        self.model.set_device(self._settings.active_device)

    def _repaint(self) -> None:
        if vp := self.view.viewport():
            vp.update()

    # --- selection -----------------------------------------------------------

    def selected(self) -> Notification | None:
        return self.model.notification(self.view.currentIndex().row())

    def _on_current_changed(self, _current: QModelIndex, _previous: QModelIndex) -> None:
        self.reply_edit.clear()
        self._update_buttons()

    def _update_buttons(self, *_args: object) -> None:
        total = self.model.rowCount()
        self.empty.setVisible(total == 0)
        self.view.setVisible(total > 0)
        self.count.setText(f"{total}" if total else "")
        n = self.selected()
        self.dismiss.setEnabled(n is not None and n.dismissable)
        self.clear_all.setEnabled(
            any(
                (m := self.model.notification(r)) is not None and m.dismissable
                for r in range(total)
            )
        )
        can_reply = n is not None and n.repliable
        self.reply_row.setVisible(can_reply)
        if n is not None and can_reply:
            self.reply_edit.setPlaceholderText(f"Reply to {n.title or n.app_name}…")
        self.reply_send.setEnabled(can_reply and bool(self.reply_edit.text().strip()))

    # --- actions -------------------------------------------------------------

    def _dismiss_selected(self) -> None:
        n = self.selected()
        if n is not None and n.dismissable:
            self._backend.dismiss_notification(self._settings.active_device, n.id)

    def _dismiss_all(self) -> None:
        device_id = self._settings.active_device
        # Collect first: each dismissal may remove a row right away and shift the rest.
        ids = [
            n.id
            for r in range(self.model.rowCount())
            if (n := self.model.notification(r)) is not None and n.dismissable
        ]
        for notification_id in ids:
            self._backend.dismiss_notification(device_id, notification_id)

    def _send_reply(self) -> None:
        n, text = self.selected(), self.reply_edit.text().strip()
        if n is None or not n.repliable or not text:
            return
        self._backend.reply_notification(self._settings.active_device, n.id, text)
        self.reply_edit.clear()
