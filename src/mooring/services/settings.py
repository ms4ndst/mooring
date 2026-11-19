"""Persistent user settings (``~/.config/mooring/Mooring.conf``)."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QSettings, pyqtSignal

from mooring.theme.catppuccin import DEFAULT_FLAVOR, FLAVORS


class SettingsStore(QObject):
    flavorChanged = pyqtSignal(str)
    activeDeviceChanged = pyqtSignal(str)

    def __init__(self, qsettings: QSettings | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._s = qsettings if qsettings is not None else QSettings()

    @property
    def flavor(self) -> str:
        value = str(self._s.value("theme/flavor", DEFAULT_FLAVOR))
        return value if value in FLAVORS else DEFAULT_FLAVOR

    def set_flavor(self, flavor: str) -> None:
        if flavor not in FLAVORS or flavor == self.flavor:
            return
        self._s.setValue("theme/flavor", flavor)
        self.flavorChanged.emit(flavor)

    @property
    def notify_show_text(self) -> bool:
        """Show message text in desktop notifications (off = just 'New message')."""
        return str(self._s.value("notifications/show_text", "true")).lower() == "true"

    def set_notify_show_text(self, show: bool) -> None:
        self._s.setValue("notifications/show_text", "true" if show else "false")

    @property
    def active_device(self) -> str:
        """Empty string when no device has been chosen."""
        return str(self._s.value("device/active", ""))

    def set_active_device(self, device_id: str) -> None:
        if device_id == self.active_device:
            return
        self._s.setValue("device/active", device_id)
        self.activeDeviceChanged.emit(device_id)
