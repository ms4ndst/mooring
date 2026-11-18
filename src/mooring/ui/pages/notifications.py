from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListView, QPushButton, QVBoxLayout, QWidget

from mooring.backend.base import PhoneBackend
from mooring.models import NotificationListModel
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from mooring.ui.pages.common import page_title
from mooring.ui.widgets.delegates import NotificationDelegate


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
        self.empty = QLabel("No notifications")
        self.empty.setObjectName("hint")
        self.dismiss = QPushButton("Dismiss")
        self.dismiss.clicked.connect(self._dismiss_selected)

        header = QHBoxLayout()
        header.addWidget(page_title("Notifications"), 1)
        header.addWidget(self.dismiss)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.addLayout(header)
        layout.addWidget(self.empty)
        layout.addWidget(self.view, 1)

        for signal in (self.model.modelReset, self.model.rowsInserted, self.model.rowsRemoved):
            signal.connect(self._update_empty)
        settings.activeDeviceChanged.connect(self.model.set_device)
        backend.devicesChanged.connect(self._reload)
        theme.changed.connect(self._repaint)
        self._reload()

    def _reload(self) -> None:
        self.model.set_device(self._settings.active_device)

    def _repaint(self) -> None:
        if vp := self.view.viewport():
            vp.update()

    def _update_empty(self) -> None:
        has_items = self.model.rowCount() > 0
        self.empty.setVisible(not has_items)
        self.dismiss.setEnabled(has_items)

    def _dismiss_selected(self) -> None:
        n = self.model.notification(self.view.currentIndex().row())
        if n is not None and n.dismissable:
            self._backend.dismiss_notification(self._settings.active_device, n.id)
