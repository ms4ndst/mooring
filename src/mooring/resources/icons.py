"""Bundled SVG icons. Sidebar icons use ``currentColor`` and are tinted per theme."""

from __future__ import annotations

from functools import cache
from importlib.resources import files

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

APP_ID = "io.github.ms4ndst.Mooring"


@cache
def svg_source(name: str) -> str:
    return files("mooring.resources").joinpath(f"icons/{name}.svg").read_text("utf-8")


def _render(svg: str, size: int) -> QPixmap:
    app = QGuiApplication.instance()
    ratio = app.devicePixelRatio() if isinstance(app, QGuiApplication) else 1.0
    pix = QPixmap(int(size * ratio), int(size * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pix


def tinted_icon(name: str, color: str, selected_color: str | None = None, size: int = 20) -> QIcon:
    """A sidebar icon in ``color`` (and ``selected_color`` when its row is selected)."""
    source = svg_source(name)
    icon = QIcon()
    icon.addPixmap(_render(source.replace("currentColor", color), size), QIcon.Mode.Normal)
    icon.addPixmap(
        _render(source.replace("currentColor", selected_color or color), size),
        QIcon.Mode.Selected,
    )
    return icon


def app_icon() -> QIcon:
    """The installed theme icon if present (RPM), else the bundled SVG."""
    themed = QIcon.fromTheme(APP_ID)
    if not themed.isNull():
        return themed
    icon = QIcon()
    source = svg_source("app")
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_render(source, size))
    return icon
