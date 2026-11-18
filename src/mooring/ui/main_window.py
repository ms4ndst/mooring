from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mooring.backend.base import PhoneBackend
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from mooring.ui.pages.common import PlaceholderPage
from mooring.ui.pages.messages import MessagesPage
from mooring.ui.pages.notifications import NotificationsPage
from mooring.ui.pages.settings import SettingsPage
from mooring.ui.widgets.device_header import DeviceHeader


class MainWindow(QMainWindow):
    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mooring")
        self.resize(1100, 720)
        self.hide_on_close = False  # set by the app when a tray icon exists

        self.messages = MessagesPage(backend, settings, theme)
        self.notifications = NotificationsPage(backend, settings, theme)
        pages: list[tuple[str, str, QWidget]] = [
            ("Messages", "mail-message", self.messages),
            (
                "Photos",
                "image-x-generic",
                PlaceholderPage("Photos", "Recent photos arrive in Phase 4."),
            ),
            ("Notifications", "preferences-desktop-notification", self.notifications),
            (
                "Files",
                "folder",
                PlaceholderPage("Files", "Browsing the phone's storage arrives in Phase 4."),
            ),
            (
                "Apps",
                "applications-other",
                PlaceholderPage("Apps", "Screen mirroring and phone apps arrive in Phase 5."),
            ),
            ("Settings", "preferences-system", SettingsPage(backend, settings)),
        ]

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.stack = QStackedWidget()
        for label, _icon, page in pages:
            QListWidgetItem(label, self.nav)  # TODO: bundled icons (theme icons are spotty)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(230)
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(0, 0, 0, 12)
        sv.setSpacing(8)
        self.device_header = DeviceHeader(backend, settings, theme)
        sv.addWidget(self.device_header)
        sv.addWidget(self.nav, 1)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(sidebar)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        if bar := self.statusBar():
            bar.setSizeGripEnabled(False)
        backend.error.connect(self.show_error)

    def show_error(self, message: str) -> None:
        if bar := self.statusBar():
            bar.showMessage(message, 8000)

    def show_page(self, label: str) -> None:
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item is not None and item.text() == label:
                self.nav.setCurrentRow(row)
                return

    def show_and_raise(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if self.hide_on_close and event is not None:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)
