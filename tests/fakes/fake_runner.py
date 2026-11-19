"""Scripted stand-in for mirror.Runner: tests never run real adb/scrcpy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

Done = Callable[[int, str, str], None]

ADB_DEVICES = (
    "List of devices attached\n"
    "FAKESERIAL01           device usb:3-7 product:fakephone model:Pixel_Fake device:fake "
    "transport_id:1\n\n"
)
APP_LIST = (
    "[server] INFO: List of apps:\n"
    " * Settings                 com.android.settings\n"
    " - Example Chat             org.example.chat\n"
    " - Maps Sample              org.example.maps\n"
    " * Camera                   com.android.camera\n"
)


class FakeProcess(QObject):
    """What spawn() returns: terminate() ends it like closing the window."""

    finished = pyqtSignal()

    def __init__(self, done: Done) -> None:
        super().__init__()
        self._done = done
        self.running = True

    def terminate(self) -> None:
        self.exit(0)

    def exit(self, code: int, output: str = "") -> None:
        if self.running:
            self.running = False
            self._done(code, "", output)


@dataclass
class FakeRunner:
    """``responses`` maps the first matching argument tuple prefix to (code, out, err)."""

    responses: dict[tuple[str, ...], tuple[int, str, str]] = field(
        default_factory=lambda: {
            ("--version",): (0, "scrcpy 5.0.1 <https://github.com/Genymobile/scrcpy>\n", ""),
            ("devices", "-l"): (0, ADB_DEVICES, ""),
            ("-s", "FAKESERIAL01", "--list-apps"): (0, "", APP_LIST),
        }
    )
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    spawned: list[tuple[tuple[str, ...], FakeProcess]] = field(default_factory=list)

    def _answer(self, args: tuple[str, ...]) -> tuple[int, str, str]:
        for prefix, result in self.responses.items():
            if args[: len(prefix)] == prefix:
                return result
        return (1, "", "error: unknown command in FakeRunner")

    def run(self, program: str, args: list[str], done: Done, timeout_ms: int = 30_000) -> None:
        self.calls.append((program, tuple(args)))
        code, out, err = self._answer(tuple(args))
        QTimer.singleShot(0, lambda: done(code, out, err))

    def spawn(self, program: str, args: list[str], done: Done) -> FakeProcess:
        self.calls.append((program, tuple(args)))
        proc = FakeProcess(done)
        self.spawned.append((tuple(args), proc))
        return proc
