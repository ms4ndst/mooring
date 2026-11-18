"""Catppuccin palettes (https://catppuccin.com/palette) and the app stylesheet."""

from __future__ import annotations

from importlib.resources import files
from string import Template

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor

_NAMES = [
    "rosewater",
    "flamingo",
    "pink",
    "mauve",
    "red",
    "maroon",
    "peach",
    "yellow",
    "green",
    "teal",
    "sky",
    "sapphire",
    "blue",
    "lavender",
    "text",
    "subtext1",
    "subtext0",
    "overlay2",
    "overlay1",
    "overlay0",
    "surface2",
    "surface1",
    "surface0",
    "base",
    "mantle",
    "crust",
]


def _palette(hexes: str) -> dict[str, str]:
    values = hexes.split()
    assert len(values) == len(_NAMES)
    return {name: f"#{v}" for name, v in zip(_NAMES, values, strict=True)}


FLAVORS: dict[str, dict[str, str]] = {
    "latte": _palette(
        "dc8a78 dd7878 ea76cb 8839ef d20f39 e64553 fe640b df8e1d 40a02b 179299 04a5e5 209fb5 "
        "1e66f5 7287fd 4c4f69 5c5f77 6c6f85 7c7f93 8c8fa1 9ca0b0 acb0be bcc0cc ccd0da "
        "eff1f5 e6e9ef dce0e8"
    ),
    "frappe": _palette(
        "f2d5cf eebebe f4b8e4 ca9ee6 e78284 ea999c ef9f76 e5c890 a6d189 81c8be 99d1db 85c1dc "
        "8caaee babbf1 c6d0f5 b5bfe2 a5adce 949cbb 838ba7 737994 626880 51576d 414559 "
        "303446 292c3c 232634"
    ),
    "macchiato": _palette(
        "f4dbd6 f0c6c6 f5bde6 c6a0f6 ed8796 ee99a0 f5a97f eed49f a6da95 8bd5ca 91d7e3 7dc4e4 "
        "8aadf4 b7bdf8 cad3f5 b8c0e0 a5adcb 939ab7 8087a2 6e738d 5b6078 494d64 363a4f "
        "24273a 1e2030 181926"
    ),
    "mocha": _palette(
        "f5e0dc f2cdcd f5c2e7 cba6f7 f38ba8 eba0ac fab387 f9e2af a6e3a1 94e2d5 89dceb 74c7ec "
        "89b4fa b4befe cdd6f4 bac2de a6adc8 9399b2 7f849c 6c7086 585b70 45475a 313244 "
        "1e1e2e 181825 11111b"
    ),
}

FLAVOR_LABELS = {"latte": "Latte", "frappe": "Frappé", "macchiato": "Macchiato", "mocha": "Mocha"}
DEFAULT_FLAVOR = "mocha"
ACCENT = "mauve"
AVATAR_COLORS = [
    "rosewater",
    "flamingo",
    "pink",
    "mauve",
    "red",
    "maroon",
    "peach",
    "yellow",
    "green",
    "teal",
    "sky",
    "sapphire",
    "blue",
    "lavender",
]


class Theme(QObject):
    """The active flavour. Widgets that paint by hand listen to ``changed``."""

    changed = pyqtSignal()

    def __init__(self, flavor: str = DEFAULT_FLAVOR, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._flavor = flavor if flavor in FLAVORS else DEFAULT_FLAVOR

    @property
    def flavor(self) -> str:
        return self._flavor

    @property
    def is_dark(self) -> bool:
        return self._flavor != "latte"

    def set_flavor(self, flavor: str) -> None:
        if flavor in FLAVORS and flavor != self._flavor:
            self._flavor = flavor
            self.changed.emit()

    def hex(self, name: str) -> str:
        if name == "accent":
            name = ACCENT
        return FLAVORS[self._flavor][name]

    def color(self, name: str) -> QColor:
        return QColor(self.hex(name))

    def stylesheet(self) -> str:
        template = Template(files("mooring.theme").joinpath("style.qss").read_text("utf-8"))
        return template.substitute(FLAVORS[self._flavor], accent=self.hex("accent"))
