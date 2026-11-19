from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent
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
from mooring.ui.pages.files import FilesPage
from mooring.ui.pages.messages import MessagesPage
from mooring.ui.pages.notifications import NotificationsPage
from mooring.ui.pages.photos import PhotosPage
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
        self._settings = settings
        self._backend = backend
        self.setAcceptDrops(True)

        self.photos = PhotosPage(backend, settings, theme)
        self.files = FilesPage(backend, settings)
        self.messages = MessagesPage(backend, settings, theme)
        self.notifications = NotificationsPage(backend, settings, theme)
        pages: list[tuple[str, str, QWidget]] = [
            ("Messages", "mail-message", self.messages),
            ("Photos", "image-x-generic", self.photos),
            ("Notifications", "preferences-desktop-notification", self.notifications),
            ("Files", "folder", self.files),
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

    # --- drop files to send them to the phone --------------------------------

    def files_to_send(self, urls: list[QUrl]) -> list[QUrl]:
        """Local files only, and never the phone's own files (e.g. dragged from Photos)."""
        root = self._backend.storage_root(self._settings.active_device)
        mount = str(Path(root).parents[2]) if root and len(Path(root).parents) > 2 else root
        result = []
        for url in urls:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if not path.is_file():
                continue
            if mount and str(path).startswith(mount):
                continue
            result.append(url)
        return result

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        mime = event.mimeData() if event else None
        device = self._backend.device(self._settings.active_device)
        usable = device is not None and device.reachable and device.trusted
        if event and mime and mime.hasUrls() and usable and self.files_to_send(mime.urls()):
            event.acceptProposedAction()
        elif event:
            event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:
        mime = event.mimeData() if event else None
        if event is None or mime is None:
            return
        urls = self.files_to_send(mime.urls())
        device_id = self._settings.active_device
        for url in urls:
            self._backend.share_url(device_id, url.toString())
        if urls:
            event.acceptProposedAction()
            self.show_message(f"Sending {len(urls)} file(s) to the phone…")

    def show_message(self, message: str) -> None:
        if bar := self.statusBar():
            bar.showMessage(message, 6000)

    def show_error(self, message: str) -> None:
        if bar := self.statusBar():
            bar.showMessage(message, 8000)

    def show_page(self, label: str) -> None:
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item is not None and item.text() == label:
                self.nav.setCurrentRow(row)
                return

    def open_thread(self, device_id: str, thread_id: int) -> None:
        """Bring the window up on a conversation (e.g. from a notification)."""
        self._settings.set_active_device(device_id)
        self.show_page("Messages")
        self.messages.open_thread(thread_id)
        self.show_and_raise()

    def is_viewing(self, device_id: str, thread_id: int) -> bool:
        """True if that thread is on screen right now (no need to notify)."""
        return (
            self.isActiveWindow()
            and self.stack.currentWidget() is self.messages
            and self._settings.active_device == device_id
            and self.messages.current_thread == thread_id
        )

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
