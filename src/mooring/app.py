"""Application entry point: QApplication, single-instance guard, tray icon."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from PyQt6.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from mooring import __version__
from mooring.backend.base import PhoneBackend
from mooring.services.notifier import Notifier
from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import FLAVORS, Theme
from mooring.ui.main_window import MainWindow

log = logging.getLogger("mooring")


class SingleInstance(QObject):
    """Owns a per-user local socket. A second launch pokes it and exits."""

    activated = pyqtSignal()

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name or f"mooring-{os.getuid()}"
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(300):
            probe.write(b"activate\n")
            probe.flush()
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)  # clear a stale socket from a crash
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(self.name):
            log.warning("single-instance socket unavailable: %s", self._server.errorString())
            return True
        self._server.newConnection.connect(self._on_connection)
        return True

    def _on_connection(self) -> None:
        assert self._server is not None
        while (conn := self._server.nextPendingConnection()) is not None:
            conn.disconnected.connect(conn.deleteLater)
            conn.close()
            self.activated.emit()


def app_icon(theme: Theme) -> QIcon:
    themed = QIcon.fromTheme("smartphone")
    if not themed.isNull():
        return themed
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.color("accent"))
    p.drawRoundedRect(QRectF(16, 4, 32, 56), 8, 8)
    p.setBrush(theme.color("crust"))
    p.drawRoundedRect(QRectF(20, 10, 24, 40), 3, 3)
    p.end()
    return QIcon(pix)


def make_tray(
    window: MainWindow, backend: PhoneBackend, settings: SettingsStore, icon: QIcon
) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(icon, window)
    tray.setToolTip("Mooring")
    menu = QMenu(window)
    show = QAction("Show Mooring", menu)
    show.triggered.connect(window.show_and_raise)
    ring = QAction("Ring phone", menu)
    ring.triggered.connect(window.device_header.ring.click)
    quit_ = QAction("Quit", menu)
    quit_.triggered.connect(QApplication.quit)
    for action in (show, ring):
        menu.addAction(action)
    menu.addSeparator()
    menu.addAction(quit_)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            window.show_and_raise() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
    )
    tray.show()
    return tray


def create_backend(fake: bool) -> PhoneBackend:
    if fake:
        from mooring.backend.fake import FakeBackend

        return FakeBackend()
    from mooring.backend.kdeconnect import KdeConnectBackend

    return KdeConnectBackend()


def ensure_active_device(backend: PhoneBackend, settings: SettingsStore) -> None:
    devices = backend.devices()
    if any(d.id == settings.active_device for d in devices):
        return
    choice = (
        next((d for d in devices if d.reachable and d.trusted), None)
        or next((d for d in devices if d.trusted), None)
        or (devices[0] if devices else None)
    )
    settings.set_active_device(choice.id if choice else "")


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(prog="mooring", description="Phone hub for Linux")
    parser.add_argument("--fake", action="store_true", help="use synthetic data, no phone needed")
    parser.add_argument("--flavor", choices=sorted(FLAVORS), help="Catppuccin flavour")
    parser.add_argument("--debug", action="store_true", help="verbose logging (IDs only)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, qt_args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Mooring")
    app.setOrganizationName("mooring")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("mooring")

    instance = SingleInstance()
    if not instance.acquire():
        log.info("Mooring is already running; asked it to come forward")
        return 0

    app.setStyle("Fusion")
    settings = SettingsStore()
    if args.flavor:
        settings.set_flavor(args.flavor)
    theme = Theme(settings.flavor)
    settings.flavorChanged.connect(theme.set_flavor)
    theme.changed.connect(lambda: app.setStyleSheet(theme.stylesheet()))
    app.setStyleSheet(theme.stylesheet())

    backend = create_backend(args.fake)
    backend.devicesChanged.connect(lambda: ensure_active_device(backend, settings))

    window = MainWindow(backend, settings, theme)
    icon = app_icon(theme)
    app.setWindowIcon(icon)
    instance.activated.connect(window.show_and_raise)

    if QSystemTrayIcon.isSystemTrayAvailable():
        window.tray = make_tray(window, backend, settings, icon)  # type: ignore[attr-defined]
        window.hide_on_close = True
        app.setQuitOnLastWindowClosed(False)
    else:
        log.info("no system tray (StatusNotifierItem host) found; closing the window quits")

    notifier = Notifier(
        backend,
        settings,
        open_thread=window.open_thread,
        is_viewing=window.is_viewing,
        parent=window,
    )
    notifier.start()

    backend.start()
    window.show()
    return app.exec()
