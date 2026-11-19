"""Render README screenshots from synthetic data only (no phone, no real data).

    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/screenshots.py [out_dir]

Uses the fake backend, a scripted adb/scrcpy runner and a drawn phone screen for the
Mirror page. Nothing here reads the user's contacts, cache, config or devices.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
SANDBOX = Path(tempfile.mkdtemp(prefix="mooring-shots-"))

# Isolate from the real user data before anything Qt or Mooring is imported.
for var in ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
    os.environ[var] = str(SANDBOX / var.lower())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from PyQt6.QtCore import QObject, QRectF, QSettings, Qt, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")

from tests.fakes.fake_runner import FakeRunner  # noqa: E402

import mooring.backend.mirror as mirror_mod  # noqa: E402
from mooring.backend.fake import PHONE_ID, FakeBackend  # noqa: E402
from mooring.services.settings import SettingsStore  # noqa: E402
from mooring.theme.catppuccin import Theme  # noqa: E402
from mooring.ui.main_window import MainWindow  # noqa: E402

APPS = (
    "[server] INFO: List of apps:\n"
    + "".join(
        f" - {name:<26} org.example.{name.lower().replace(' ', '')}\n"
        for name in (
            "Chat",
            "Maps",
            "Music",
            "Notes",
            "Weather",
            "Calendar",
            "Podcasts",
            "Camera Plus",
            "Banking (fake)",
            "Recipes",
        )
    )
    + " * Settings                   com.android.settings\n"
    + " * Clock                      com.android.deskclock\n"
)


def phone_home_screen(w: int = 540, h: int = 1140) -> QImage:
    """A made-up Android home screen in Catppuccin colours."""
    img = QImage(w, h, QImage.Format.Format_RGB888)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, 0, h)
    grad.setColorAt(0, QColor("#cba6f7"))
    grad.setColorAt(0.55, QColor("#89b4fa"))
    grad.setColorAt(1, QColor("#1e1e2e"))
    p.fillRect(0, 0, w, h, grad)
    p.setPen(QColor("#11111b"))
    p.setFont(QFont("Sans", 14))
    p.drawText(QRectF(24, 10, w - 48, 30), Qt.AlignmentFlag.AlignLeft, "12:30")
    p.drawText(QRectF(24, 10, w - 48, 30), Qt.AlignmentFlag.AlignRight, "82%")
    p.setPen(QColor("#ffffff"))
    p.setFont(QFont("Sans", 64, QFont.Weight.Light))
    p.drawText(QRectF(0, 120, w, 110), Qt.AlignmentFlag.AlignCenter, "12:30")
    p.setFont(QFont("Sans", 16))
    p.drawText(QRectF(0, 225, w, 40), Qt.AlignmentFlag.AlignCenter, "Thursday, 19 November")
    colours = [
        "#f38ba8",
        "#fab387",
        "#f9e2af",
        "#a6e3a1",
        "#94e2d5",
        "#89dceb",
        "#b4befe",
        "#f5c2e7",
    ]
    labels = ["Chat", "Maps", "Music", "Notes", "Weather", "Calendar", "Camera", "Clock"]
    size, gap = 84, (w - 4 * 84) / 5
    for i, (c, label) in enumerate(zip(colours, labels, strict=True)):
        x = gap + (i % 4) * (size + gap)
        y = 640 + (i // 4) * (size + 70)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c))
        p.drawRoundedRect(QRectF(x, y, size, size), 24, 24)
        p.setPen(QColor("#11111b"))
        p.setFont(QFont("Sans", 26, QFont.Weight.Bold))
        p.drawText(QRectF(x, y, size, size), Qt.AlignmentFlag.AlignCenter, label[0])
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Sans", 12))
        p.drawText(QRectF(x - 20, y + size + 6, size + 40, 24), Qt.AlignmentFlag.AlignCenter, label)
    p.setBrush(QColor(255, 255, 255, 60))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(w / 2 - 70, h - 30, 140, 8), 4, 4)
    p.end()
    return img


class ScreenshotSession(QObject):
    """Stands in for MirrorSession: shows one drawn frame, sends input nowhere."""

    started = pyqtSignal(str)
    frameReady = pyqtSignal()
    sizeChanged = pyqtSignal(int, int)
    stopped = pyqtSignal(str)

    def __init__(self, frame: QImage) -> None:
        super().__init__()
        self._frame, self.video_size, self.frames = frame, (frame.width(), frame.height()), 1

    running = True

    def latest_frame(self) -> QImage:
        return self._frame

    def start(self) -> None:
        QTimer.singleShot(0, lambda: (self.started.emit("Phone"), self.frameReady.emit()))

    def stop(self) -> None:
        QTimer.singleShot(0, lambda: self.stopped.emit(""))

    def send(self, _message: bytes) -> None:
        pass


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    mirror_mod.shutil.which = lambda name: f"/fake/bin/{name}"  # type: ignore[assignment]
    mirror_mod.find_server = lambda: "/fake/share/scrcpy-server"
    runner = FakeRunner()
    runner.responses[("-s", "FAKESERIAL01", "--list-apps")] = (0, "", APPS)

    settings = SettingsStore(QSettings(str(SANDBOX / "settings.ini"), QSettings.Format.IniFormat))
    settings.set_active_device(PHONE_ID)
    theme = Theme("mocha")
    app.setStyleSheet(theme.stylesheet())
    backend = FakeBackend(auto_reply_ms=None, storage_parent=str(SANDBOX))
    phone = backend._devices[PHONE_ID]
    backend._devices[PHONE_ID] = replace(phone, name="Pixel 8")
    mirror = mirror_mod.MirrorBackend(runner)
    frame = phone_home_screen()
    mirror.session_factory = lambda serial: ScreenshotSession(frame)  # type: ignore[assignment,return-value]

    window = MainWindow(backend, settings, theme, mirror=mirror)
    window.resize(1180, 740)
    backend.start()
    window.show()

    shots: list[tuple[int, str, object]] = []

    def at(ms: int, name: str, action: object) -> None:
        shots.append((ms, name, action))

    def grab(name: str) -> None:
        window.grab().save(str(OUT / f"{name}.png"))
        print(f"wrote {OUT / name}.png")

    at(300, "", lambda: window.open_thread(PHONE_ID, 1001))
    at(900, "messages", None)
    at(1000, "", lambda: window.show_page("Photos"))
    at(2600, "", lambda: window.photos.view.setCurrentIndex(window.photos.model.index(2)))
    at(2800, "photos", None)
    at(2900, "", lambda: window.show_page("Notifications"))
    at(
        3000,
        "",
        lambda: window.notifications.view.setCurrentIndex(window.notifications.model.index(1)),
    )
    at(3200, "notifications", None)
    at(3300, "", lambda: window.show_page("Mirror"))
    at(4000, "mirror", None)
    at(4100, "", lambda: window.show_page("Apps"))
    at(4900, "", lambda: window.apps.app_list.setCurrentRow(0))
    at(5100, "apps", None)
    at(
        5200,
        "",
        lambda: (
            settings.set_flavor("latte"),
            theme.set_flavor("latte"),
            app.setStyleSheet(theme.stylesheet()),
            window.show_page("Messages"),
        ),
    )
    at(5800, "messages-latte", None)
    at(6000, "", app.quit)
    for ms, name, action in shots:
        QTimer.singleShot(ms, (lambda n=name: grab(n)) if action is None else action)  # type: ignore[arg-type]
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
