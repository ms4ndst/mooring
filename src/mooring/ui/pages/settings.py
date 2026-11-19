from __future__ import annotations

from collections.abc import Callable

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
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import FLAVOR_LABELS
from mooring.ui.pages.common import page_title


def _device_label(d: Device) -> str:
    if not d.trusted:
        return f"{d.name} (not paired)"
    return d.name if d.reachable else f"{d.name} (offline)"


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
        form.addRow(QLabel("Notifications"), self.show_text)
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
