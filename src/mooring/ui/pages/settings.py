from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtGui import QPixmapCache, QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mooring.backend.base import Device, PhoneBackend
from mooring.services.desktop import autostart_enabled, set_autostart
from mooring.services.paths import cache_size, clear_cache
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import FLAVOR_LABELS
from mooring.ui.pages.common import page_title


def _device_label(d: Device) -> str:
    if not d.trusted:
        return f"{d.name} (not paired)"
    return d.name if d.reachable else f"{d.name} (offline)"


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} GB"


class SettingsPage(QWidget):
    def __init__(
        self, backend: PhoneBackend, settings: SettingsStore, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings = backend, settings
        #: Asked before unpairing; replaced in tests.
        self.confirm_unpair: Callable[[str], bool] = self._ask_unpair

        self.device_combo = QComboBox()
        self.pair_button = QPushButton("Pair")
        self.pair_hint = QLabel()
        self.pair_hint.setObjectName("muted")
        self.pair_hint.setWordWrap(True)
        self.show_text = QCheckBox("Show message text in notifications")
        self.show_text.setChecked(settings.notify_show_text)
        self.show_text.toggled.connect(settings.set_notify_show_text)
        self.autostart = QCheckBox("Start Mooring when I log in (in the tray)")
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self._on_autostart_toggled)
        self.clipboard_sync = QCheckBox("Sync clipboard automatically (KDE Connect)")
        self.clipboard_sync.toggled.connect(self._on_clipboard_toggled)
        self.cache_label = QLabel()
        self.cache_label.setObjectName("muted")
        self.clear_cache_button = QPushButton("Clear cache")
        self.clear_cache_button.clicked.connect(self._clear_cache)
        self.flavor_combo = QComboBox()
        for key, label in FLAVOR_LABELS.items():
            self.flavor_combo.addItem(label, key)
        self.flavor_combo.setCurrentIndex(self.flavor_combo.findData(settings.flavor))

        pair_row = QHBoxLayout()
        pair_row.addWidget(self.pair_button)
        pair_row.addWidget(self.pair_hint, 1)

        form = QFormLayout()
        form.setSpacing(12)
        form.addRow(QLabel("Active device"), self.device_combo)
        form.addRow(QLabel("Pairing"), pair_row)
        form.addRow(QLabel("Startup"), self.autostart)
        form.addRow(QLabel("Notifications"), self.show_text)
        form.addRow(QLabel("Clipboard"), self.clipboard_sync)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.clear_cache_button)
        cache_row.addWidget(self.cache_label, 1)
        form.addRow(QLabel("Cache"), cache_row)
        form.addRow(QLabel("Theme"), self.flavor_combo)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)
        layout.addWidget(page_title("Settings"))
        layout.addLayout(form)
        layout.addStretch(1)

        self.flavor_combo.currentIndexChanged.connect(self._on_flavor_picked)
        self.device_combo.currentIndexChanged.connect(self._on_device_picked)
        self.pair_button.clicked.connect(self._on_pair_clicked)
        backend.devicesChanged.connect(self._fill_devices)
        backend.deviceChanged.connect(self._fill_devices)
        settings.activeDeviceChanged.connect(self._fill_devices)
        self._fill_devices()

    def _on_flavor_picked(self, _index: int) -> None:
        self._settings.set_flavor(self.flavor_combo.currentData())

    def _fill_devices(self, *_args: object) -> None:
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for d in self._backend.devices():
            self.device_combo.addItem(_device_label(d), d.id)
        self.device_combo.setCurrentIndex(self.device_combo.findData(self._settings.active_device))
        self.device_combo.blockSignals(False)
        self._update_pair_button()
        self._update_clipboard()

    def _update_pair_button(self) -> None:
        device = self._backend.device(self._settings.active_device)
        if device is None:
            self.pair_button.setEnabled(False)
            self.pair_button.setText("Pair")
            self.pair_hint.setText("Open KDE Connect on the phone, on the same network.")
        elif device.trusted:
            self.pair_button.setEnabled(True)
            self.pair_button.setText("Unpair")
            self.pair_hint.setText("")
        else:
            self.pair_button.setEnabled(device.reachable)
            self.pair_button.setText("Pair")
            self.pair_hint.setText(
                "Accept the request on the phone." if device.reachable else "Device is offline."
            )

    def _update_clipboard(self) -> None:
        device = self._backend.device(self._settings.active_device)
        known = device is not None and device.trusted and device.clipboard_sync is not None
        self.clipboard_sync.blockSignals(True)
        self.clipboard_sync.setChecked(bool(device and device.clipboard_sync))
        self.clipboard_sync.blockSignals(False)
        self.clipboard_sync.setEnabled(known)

    def _on_autostart_toggled(self, enabled: bool) -> None:
        try:
            set_autostart(enabled)
        except OSError:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(autostart_enabled())
            self.autostart.blockSignals(False)

    def _on_clipboard_toggled(self, enabled: bool) -> None:
        if device_id := self._settings.active_device:
            self._backend.set_clipboard_sync(device_id, enabled)

    def showEvent(self, event: QShowEvent | None) -> None:
        super().showEvent(event)
        self._update_cache_label()

    def _update_cache_label(self) -> None:
        size = cache_size()
        self.cache_label.setText(
            f"{human_size(size)} of thumbnails in ~/.cache/mooring" if size else "Empty"
        )
        self.clear_cache_button.setEnabled(size > 0)

    def _clear_cache(self) -> None:
        clear_cache()
        QPixmapCache.clear()
        self._update_cache_label()

    def _on_device_picked(self, _index: int) -> None:
        device_id = self.device_combo.currentData()
        if device_id:
            self._settings.set_active_device(device_id)

    def _on_pair_clicked(self) -> None:
        device = self._backend.device(self._settings.active_device)
        if device is None:
            return
        if not device.trusted:
            self._backend.request_pairing(device.id)
            self.pair_hint.setText("Pairing requested. Accept it on the phone.")
        elif self.confirm_unpair(device.name):
            self._backend.unpair(device.id)

    def _ask_unpair(self, name: str) -> bool:
        answer = QMessageBox.question(
            self,
            "Unpair device",
            f"Unpair {name}? You'll need the phone in hand to pair again.",
        )
        return answer == QMessageBox.StandardButton.Yes
