"""Mirror: the phone's screen inside Mooring, controllable with mouse and keyboard.

Starts when the page is opened and stops when it's left (or the window is hidden):
streaming keeps the phone awake and costs its battery.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QHideEvent, QShowEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mooring.backend import scrcpy_protocol as sp
from mooring.backend.base import PhoneBackend
from mooring.backend.mirror import MirrorBackend, Tools
from mooring.backend.scrcpy_client import MirrorSession
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from mooring.ui.pages.adb_common import chosen_device, setup_help
from mooring.ui.pages.common import page_title
from mooring.ui.widgets.phone_view import PhoneView


class MirrorPage(QWidget):
    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        mirror: MirrorBackend,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings, self.mirror = backend, settings, mirror
        self.session: MirrorSession | None = None
        #: The user pressed Stop on this visit: don't restart until they press Start.
        self._user_stopped = False

        self.status = QLabel()
        self.status.setObjectName("muted")
        self.refresh_button = QPushButton("Refresh")
        self.popout_button = QPushButton("Pop out")
        self.popout_button.setToolTip("Mirror in a separate scrcpy window instead")
        self.toggle_button = QPushButton("Stop")
        self.toggle_button.setObjectName("primary")
        header = QHBoxLayout()
        header.addWidget(page_title("Mirror"))
        header.addWidget(self.status, 1)
        for b in (self.refresh_button, self.popout_button, self.toggle_button):
            header.addWidget(b)

        self.help = QLabel()
        self.help.setObjectName("hint")
        self.help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.help.setWordWrap(True)
        self.help.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.phone_view = PhoneView(theme)
        self.back_button = QPushButton("Back")
        self.home_button = QPushButton("Home")
        self.recents_button = QPushButton("Recents")
        self.rotate_button = QPushButton("Rotate")
        nav = QHBoxLayout()
        nav.addStretch(1)
        for b in (self.back_button, self.home_button, self.recents_button, self.rotate_button):
            nav.addWidget(b)
        nav.addStretch(1)
        screen = QWidget()
        sv = QVBoxLayout(screen)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(self.phone_view, 1)
        sv.addLayout(nav)

        self.body = QStackedWidget()
        self.body.addWidget(self.help)
        self.body.addWidget(screen)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.body, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.toggle_button.clicked.connect(self.toggle)
        self.popout_button.clicked.connect(self._pop_out)
        self.back_button.clicked.connect(lambda: self.phone_view.press_key(sp.Keycode.BACK))
        self.home_button.clicked.connect(lambda: self.phone_view.press_key(sp.Keycode.HOME))
        self.recents_button.clicked.connect(
            lambda: self.phone_view.press_key(sp.Keycode.APP_SWITCH)
        )
        self.rotate_button.clicked.connect(self._rotate)
        mirror.devicesChanged.connect(self._on_adb_devices)
        self._update_buttons()

    # --- visibility: start on show, stop on hide -----------------------------

    def showEvent(self, event: QShowEvent | None) -> None:
        super().showEvent(event)
        self._user_stopped = False
        self.refresh()

    def hideEvent(self, event: QHideEvent | None) -> None:
        super().hideEvent(event)
        if not self.isVisible():
            self.stop()

    def refresh(self) -> None:
        """Find the tools and the phone, then start mirroring if possible."""
        self.status.setText("Looking for the phone…")
        if self.mirror.tools.ready:
            self.mirror.refresh_devices()
        else:
            self.mirror.detect_tools(self._on_tools)

    def _on_tools(self, tools: Tools) -> None:
        if tools.ready:
            self.mirror.refresh_devices()
        else:
            self._on_adb_devices()

    def _on_adb_devices(self) -> None:
        if not self.isVisible():
            return
        problem = setup_help(self.mirror)
        if problem is not None and self.session is None:
            self.help.setText(problem)
            self.body.setCurrentIndex(0)
            self.status.setText("")
        elif self.session is None and not self._user_stopped:
            self.start()
        self._update_buttons()

    # --- session -------------------------------------------------------------

    def start(self) -> None:
        if self.session is not None:
            return
        device = chosen_device(self.mirror, self._backend, self._settings)
        if device is None:
            return
        session = self.mirror.new_session(device.serial)
        if session is None:
            self.status.setText("")
            return
        self._user_stopped = False
        self.session = session
        session.started.connect(self._on_started)
        session.stopped.connect(self._on_stopped)
        self.phone_view.set_session(session)
        self.body.setCurrentIndex(1)
        self.status.setText(f"Connecting to {device.label}…")
        self._update_buttons()
        session.start()

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()  # _on_stopped tidies up when the thread ends

    def toggle(self) -> None:
        if self.session is not None:
            self._user_stopped = True
            self.stop()
        else:
            self._user_stopped = False
            self.start()

    def _on_started(self, _name: str) -> None:
        self.status.setText("Click to tap, scroll to swipe, type to enter text")
        self.phone_view.setFocus()

    def _on_stopped(self, reason: str) -> None:
        if self.sender() is not self.session:
            return
        self.session = None
        self.phone_view.set_session(None)
        self.phone_view.status_text = "Stopped"
        self.status.setText(f"Stopped: {reason}" if reason else "Stopped")
        self._update_buttons()
        if reason:
            self.mirror.error.emit(f"Mirroring stopped: {reason}")

    def _update_buttons(self) -> None:
        running = self.session is not None
        has_device = chosen_device(self.mirror, self._backend, self._settings) is not None
        self.toggle_button.setText("Stop" if running else "Start")
        self.toggle_button.setEnabled(running or has_device)
        self.popout_button.setEnabled(has_device and self.mirror.tools.ready)
        for b in (self.back_button, self.home_button, self.recents_button, self.rotate_button):
            b.setEnabled(running)

    def _rotate(self) -> None:
        if self.session is not None:
            self.session.send(sp.simple(sp.MsgType.ROTATE_DEVICE))

    def _pop_out(self) -> None:
        """Hand over to a separate scrcpy window (e.g. to give it its own Niri column)."""
        device = chosen_device(self.mirror, self._backend, self._settings)
        if device is None:
            return
        self._user_stopped = True
        self.stop()
        kde = self._backend.device(self._settings.active_device)
        name = kde.name if kde else device.model or "Phone"
        self.mirror.mirror(device.serial, f"{name} · Mooring")
