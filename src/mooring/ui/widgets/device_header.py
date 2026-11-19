from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from mooring.backend.base import PhoneBackend
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme


class DeviceHeader(QWidget):
    """Name, connection dot, battery and a Ring button for the active device."""

    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings, self._theme = backend, settings, theme

        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.name = QLabel("No device")
        self.name.setObjectName("deviceName")
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.ring = QPushButton("Ring phone")
        self.ring.clicked.connect(self._on_ring)
        self.clipboard = QPushButton("Send clipboard")
        self.clipboard.clicked.connect(self._on_clipboard)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.dot)
        top.addWidget(self.name, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.status)
        layout.addWidget(self.ring)
        layout.addWidget(self.clipboard)

        backend.devicesChanged.connect(self.refresh)
        backend.deviceChanged.connect(self._on_device_changed)
        settings.activeDeviceChanged.connect(self.refresh)
        theme.changed.connect(self.refresh)
        self.refresh()

    def _on_device_changed(self, device_id: str) -> None:
        if device_id == self._settings.active_device:
            self.refresh()

    def _on_ring(self) -> None:
        if device_id := self._settings.active_device:
            self._backend.ring(device_id)

    def _on_clipboard(self) -> None:
        if device_id := self._settings.active_device:
            self._backend.send_clipboard(device_id)

    def refresh(self) -> None:
        device = self._backend.device(self._settings.active_device)
        usable = bool(device and device.reachable and device.trusted)
        if device is None:
            color, name, status = "red", "No device", "Pair a phone in Settings"
        elif not device.trusted:
            color, name, status = "yellow", device.name, "Not paired"
        elif not device.reachable:
            color, name, status = "red", device.name, "Disconnected"
        else:
            color, name, status = "green", device.name, "Connected"
            if device.links:
                status += " via " + " + ".join(device.links)
            if device.battery is not None and device.battery >= 0:
                status += f" · {device.battery}%" + (" · charging" if device.charging else "")
        self.dot.setStyleSheet(f"background: {self._theme.hex(color)}; border-radius: 5px;")
        self.dot.setToolTip(status)
        self.name.setText(name)
        self.status.setText(status)
        self.ring.setEnabled(usable)
        clip_on = device is not None and device.clipboard_sync is not False
        self.clipboard.setEnabled(usable and clip_on)
        self.clipboard.setToolTip(
            "Send this computer's clipboard to the phone"
            if clip_on
            else "Clipboard sync is turned off in Settings"
        )
