"""Apps: mirror the phone's screen, or open a single phone app in its own window."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mooring.backend.base import PhoneBackend
from mooring.backend.mirror import AdbDevice, MirrorBackend, PhoneApp, Tools, pick_adb_device
from mooring.services.settings import SettingsStore
from mooring.ui.pages.common import page_title
from mooring.ui.widgets.pair_adb import PairAdbDialog

SETUP_TOOLS = (
    "Mirroring needs adb and scrcpy:\n\n"
    "sudo dnf install android-tools scrcpy\n\n"
    "Then press Refresh."
)
SETUP_DEVICE = (
    "No phone found over ADB.\n\n"
    "1. On the phone: Settings → About phone → tap “Build number” 7 times.\n"
    "2. Settings → System → Developer options → turn on USB debugging\n"
    "    (or Wireless debugging, then use “Pair over Wi-Fi”).\n"
    "3. Connect the USB cable and allow this computer on the phone.\n\n"
    "Then press Refresh."
)
SETUP_UNAUTHORIZED = (
    "The phone hasn't allowed this computer yet.\n\n"
    "Unlock the phone and accept “Allow USB debugging?”, then press Refresh."
)


class AppsPage(QWidget):
    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        mirror: MirrorBackend,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings, self.mirror = backend, settings, mirror
        self._started = False
        self._apps: list[PhoneApp] = []
        #: Returns (pair address, code, connect address) or None; replaced in tests.
        self.ask_pairing: Callable[[], tuple[str, str, str] | None] = self._ask_pairing

        self.status = QLabel()
        self.status.setObjectName("muted")
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(200)
        self.refresh_button = QPushButton("Refresh")
        self.pair_button = QPushButton("Pair over Wi-Fi")
        self.mirror_button = QPushButton("Mirror screen")
        self.mirror_button.setObjectName("primary")

        header = QHBoxLayout()
        header.addWidget(page_title("Apps"))
        header.addWidget(self.status, 1)
        header.addWidget(self.device_combo)
        for b in (self.refresh_button, self.pair_button, self.mirror_button):
            header.addWidget(b)

        # Page 0: setup help. Page 1: app list.
        self.help = QLabel()
        self.help.setObjectName("hint")
        self.help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.help.setWordWrap(True)
        self.help.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search apps")
        self.filter.setClearButtonEnabled(True)
        self.app_list = QListWidget()
        self.app_list.setObjectName("appList")
        self.app_list.setMouseTracking(True)
        self.open_button = QPushButton("Open in its own window")
        self.windows_label = QLabel()
        self.windows_label.setObjectName("muted")
        self.close_windows = QPushButton("Close all windows")
        bottom = QHBoxLayout()
        bottom.addWidget(self.open_button)
        bottom.addWidget(self.windows_label, 1)
        bottom.addWidget(self.close_windows)
        apps_box = QWidget()
        av = QVBoxLayout(apps_box)
        av.setContentsMargins(0, 0, 0, 0)
        av.addWidget(self.filter)
        av.addWidget(self.app_list, 1)
        av.addLayout(bottom)

        self.body = QStackedWidget()
        self.body.addWidget(self.help)
        self.body.addWidget(apps_box)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.body, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.pair_button.clicked.connect(self._pair)
        self.mirror_button.clicked.connect(self._mirror)
        self.open_button.clicked.connect(self._open_selected)
        self.close_windows.clicked.connect(mirror.close_all)
        self.app_list.itemActivated.connect(lambda _i: self._open_selected())
        self.app_list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.filter.textChanged.connect(self._fill_apps)
        self.device_combo.activated.connect(self._on_device_picked)
        mirror.devicesChanged.connect(self._on_adb_devices)
        mirror.appsLoaded.connect(self._on_apps)
        mirror.windowsChanged.connect(self._on_windows)
        mirror.paired.connect(self._on_paired)
        settings.activeDeviceChanged.connect(lambda _d: self._on_adb_devices())
        self._on_windows(mirror.window_count())
        self._update_buttons()

    # --- loading -------------------------------------------------------------

    def showEvent(self, event: QShowEvent | None) -> None:
        super().showEvent(event)
        if not self._started:
            self._started = True
            self.refresh()

    def refresh(self) -> None:
        self.status.setText("Looking for the phone…")
        self.mirror.detect_tools(self._on_tools)

    def _on_tools(self, tools: Tools) -> None:
        if not tools.ready:
            self._show_help(SETUP_TOOLS)
            self.status.setText("")
            self._fill_devices([])
            return
        self.mirror.refresh_devices()

    def _on_adb_devices(self) -> None:
        if not self.mirror.tools.ready:
            return
        devices = self.mirror.devices()
        self._fill_devices(devices)
        chosen = self.current_device()
        if chosen is None:
            unauthorized = any(d.state == "unauthorized" for d in devices)
            self._show_help(SETUP_UNAUTHORIZED if unauthorized else SETUP_DEVICE)
            self.status.setText("")
            return
        self.body.setCurrentIndex(1)
        if not self._apps:
            self.status.setText("Loading apps…")
            self.mirror.load_apps(chosen.serial)
        self._update_buttons()

    def _fill_devices(self, devices: list[AdbDevice]) -> None:
        kde_id = self._settings.active_device
        kde = self._backend.device(kde_id)
        best = pick_adb_device(
            devices, kde.name if kde else "", self._settings.adb_serial_for(kde_id)
        )
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for d in devices:
            self.device_combo.addItem(d.label, d.serial)
        if best is not None:
            self.device_combo.setCurrentIndex(self.device_combo.findData(best.serial))
        self.device_combo.blockSignals(False)
        self.device_combo.setVisible(len(devices) > 1)

    def current_device(self) -> AdbDevice | None:
        serial = self.device_combo.currentData()
        return next((d for d in self.mirror.devices() if d.serial == serial and d.usable), None)

    def _on_device_picked(self, _index: int) -> None:
        device = self.current_device()
        if device is None:
            return
        if kde_id := self._settings.active_device:
            self._settings.set_adb_serial(kde_id, device.serial)
        self._apps = []
        self._fill_apps()
        self.status.setText("Loading apps…")
        self.mirror.load_apps(device.serial)

    def _on_apps(self, serial: str, apps: list[PhoneApp]) -> None:
        device = self.current_device()
        if device is None or device.serial != serial:
            return
        self._apps = apps
        self._fill_apps()
        own = sum(1 for a in apps if not a.system)
        self.status.setText(f"{own} apps · {len(apps) - own} system")

    def _fill_apps(self, *_args: object) -> None:
        needle = self.filter.text().strip().casefold()
        self.app_list.clear()
        for app in self._apps:
            if needle and needle not in app.name.casefold() and needle not in app.package:
                continue
            item = QListWidgetItem(f"{app.name}   ·   system" if app.system else app.name)
            item.setData(Qt.ItemDataRole.UserRole, app)
            item.setToolTip(app.package)
            self.app_list.addItem(item)
        self._update_buttons()

    def _show_help(self, text: str) -> None:
        self.help.setText(text)
        self.body.setCurrentIndex(0)
        self._update_buttons()

    # --- actions -------------------------------------------------------------

    def _update_buttons(self) -> None:
        has_device = self.current_device() is not None
        self.mirror_button.setEnabled(has_device)
        self.pair_button.setEnabled(bool(self.mirror.tools.adb))
        self.open_button.setEnabled(has_device and self.app_list.currentItem() is not None)

    def _mirror(self) -> None:
        device = self.current_device()
        if device is None:
            return
        kde = self._backend.device(self._settings.active_device)
        name = kde.name if kde else device.model or "Phone"
        self.mirror.mirror(device.serial, f"{name} · Mooring")

    def _open_selected(self) -> None:
        device, item = self.current_device(), self.app_list.currentItem()
        if device is None or item is None:
            return
        app: PhoneApp = item.data(Qt.ItemDataRole.UserRole)
        self.mirror.launch_app(device.serial, app)

    def _on_windows(self, count: int) -> None:
        self.windows_label.setText(
            "" if count == 0 else f"{count} window{'s' if count != 1 else ''} open"
        )
        self.close_windows.setVisible(count > 0)

    def _pair(self) -> None:
        result = self.ask_pairing()
        if result is None:
            return
        self.status.setText("Pairing…")
        self.mirror.pair(*result)

    def _ask_pairing(self) -> tuple[str, str, str] | None:
        dialog = PairAdbDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result_value()
        return None

    def _on_paired(self, ok: bool, message: str) -> None:
        self.status.setText(message if ok else f"Pairing failed: {message}")
