from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


def page_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("pageTitle")
    return label


class PlaceholderPage(QWidget):
    """Stand-in for a page that a later phase fills in."""

    def __init__(self, title: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.addWidget(page_title(title))
        body = QLabel(hint)
        body.setObjectName("hint")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(body, 1)
