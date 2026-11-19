"""Files: open the phone's storage in the file manager; drop files to send them."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from mooring.backend.base import PhoneBackend
from mooring.services.settings import SettingsStore
from mooring.ui.pages.common import page_title


class FilesPage(QWidget):
    def __init__(
        self, backend: PhoneBackend, settings: SettingsStore, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings = backend, settings
        self._want_open = False

        self.browse_button = QPushButton("Open phone storage")
        self.browse_button.setObjectName("primary")
        self.status = QLabel()
        self.status.setObjectName("muted")
        drop = QLabel(
            "Drop files anywhere on this window to send them to the phone.\n"
            "They arrive in the phone's Download folder."
        )
        drop.setObjectName("hint")
        drop.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout()
        row.addWidget(self.browse_button)
        row.addWidget(self.status, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(page_title("Files"))
        layout.addLayout(row)
        layout.addWidget(drop, 1)

        self.browse_button.clicked.connect(self.browse)
        backend.storageReady.connect(self._on_storage_ready)
        backend.storageFailed.connect(self._on_storage_failed)

    def browse(self) -> None:
        device_id = self._settings.active_device
        device = self._backend.device(device_id)
        if device is None or not device.reachable or not device.trusted:
            self.status.setText("Phone not connected")
            return
        # Always ask for the mount: cheap when it's up, and it repairs a dropped mount.
        self._want_open = True
        self.status.setText("Opening phone storage…")
        self._backend.mount_storage(device_id)

    def _on_storage_ready(self, device_id: str, root: str) -> None:
        if self._want_open and device_id == self._settings.active_device:
            self._want_open = False
            self._open(root)

    def _on_storage_failed(self, device_id: str, reason: str) -> None:
        if self._want_open and device_id == self._settings.active_device:
            self._want_open = False
            self.status.setText(reason)

    def _open(self, root: str) -> None:
        self.status.setText("")
        QDesktopServices.openUrl(QUrl.fromLocalFile(root))
