from __future__ import annotations

import re

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

_NUMBER = re.compile(r"^\+?[0-9][0-9 ()\-]{2,}$")


def normalize_number(raw: str) -> str | None:
    """'+46 70-123 45 67' -> '+46701234567'; None if it doesn't look like a number."""
    raw = raw.strip()
    if not _NUMBER.match(raw):
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return ("+" if raw.startswith("+") else "") + digits if len(digits) >= 3 else None


class NewMessageDialog(QDialog):
    """Ask for a phone number and a first message."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New message")
        self.setMinimumWidth(380)
        self.number = QLineEdit()
        self.number.setPlaceholderText("+46 70 123 45 67")
        self.number.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\+?[0-9 ()\-]{0,24}"), self.number)
        )
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Message")
        self.text.setFixedHeight(110)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok is not None
        ok.setText("Send")
        ok.setObjectName("primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel("To"))
        layout.addWidget(self.number)
        layout.addWidget(QLabel("Message"))
        layout.addWidget(self.text)
        layout.addWidget(self.buttons)

        self.number.textChanged.connect(self._validate)
        self.text.textChanged.connect(self._validate)
        self._validate()

    def result_value(self) -> tuple[str, str] | None:
        number = normalize_number(self.number.text())
        text = self.text.toPlainText().strip()
        return (number, text) if number and text else None

    def _validate(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self.result_value() is not None)
