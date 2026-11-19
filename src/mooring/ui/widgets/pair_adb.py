from __future__ import annotations

import re

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

_ADDRESS = re.compile(r"^[A-Za-z0-9.\-\[\]:]+:\d{2,5}$")


def valid_address(text: str) -> bool:
    return bool(_ADDRESS.match(text.strip()))


class PairAdbDialog(QDialog):
    """Wireless debugging: pairing address + 6-digit code, and the connect address."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pair over Wi-Fi")
        self.setMinimumWidth(420)
        steps = QLabel(
            "On the phone: Settings → System → Developer options → Wireless debugging.\n"
            "Tap “Pair device with pairing code” and copy the address and code below.\n"
            "The connect address is the “IP address & Port” on the Wireless debugging screen."
        )
        steps.setObjectName("muted")
        steps.setWordWrap(True)
        self.pair_address = QLineEdit()
        self.pair_address.setPlaceholderText("192.168.1.20:37099")
        self.code = QLineEdit()
        self.code.setPlaceholderText("123456")
        self.code.setMaxLength(6)
        self.connect_address = QLineEdit()
        self.connect_address.setPlaceholderText("192.168.1.20:41235 (optional)")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok is not None
        ok.setText("Pair")
        ok.setObjectName("primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(steps)
        for label, field in (
            ("Pairing address", self.pair_address),
            ("Pairing code", self.code),
            ("Connect address", self.connect_address),
        ):
            layout.addWidget(QLabel(label))
            layout.addWidget(field)
        layout.addWidget(self.buttons)
        for field in (self.pair_address, self.code, self.connect_address):
            field.textChanged.connect(self._validate)
        self._validate()

    def result_value(self) -> tuple[str, str, str] | None:
        pair = self.pair_address.text().strip()
        code = self.code.text().strip()
        connect = self.connect_address.text().strip()
        if not valid_address(pair) or not re.fullmatch(r"\d{6}", code):
            return None
        if connect and not valid_address(connect):
            return None
        return pair, code, connect

    def _validate(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self.result_value() is not None)
