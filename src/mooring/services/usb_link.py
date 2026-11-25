"""Reach KDE Connect over the USB cable when the phone is on another network.

KDE Connect's LAN link needs the phone and this computer on the same network. With the
phone plugged in (USB debugging on), the link can run through adb instead:

1. ``adb reverse tcp:1764 tcp:1716``: on the phone, 127.0.0.1:1764 leads to kdeconnectd.
2. Send the phone our identity packet over UDP to its own 127.0.0.1:1716, with
   ``tcpPort`` 1764. That is what kdeconnectd's broadcast looks like, so the phone
   connects back to 127.0.0.1:1764, i.e. through the cable to kdeconnectd. TLS and
   pairing then work as usual: an unpaired or spoofed peer gets nowhere.

Only the main link goes through. Photos and files (sftp) and file transfers open extra
connections to the phone's Wi-Fi address, so they still need a shared network.

Phone port 1764 is the top of KDE Connect's range (1716-1764): the phone's own servers
take the lowest free ports, so the top one is the least likely to clash.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shlex
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QObject, QTimer

from mooring.backend.base import PhoneBackend
from mooring.backend.mirror import Done, parse_adb_devices

log = logging.getLogger(__name__)

KDECONNECT_PORT = 1716
PHONE_PORT = 1764
POLL_MS = 15_000

_TCP_ESTABLISHED = "01"
_LOOPBACK = {
    "0100007F",  # 127.0.0.1
    "0000000000000000FFFF00000100007F",  # ::ffff:127.0.0.1 (kdeconnectd listens dual-stack)
    "00000000000000000000000001000000",  # ::1
}


class RunnerLike(Protocol):
    def run(self, program: str, args: list[str], done: Done, timeout_ms: int = ...) -> None: ...


def identity_packet(device_id: str, name: str, now_ms: int | None = None) -> str:
    """kdeconnectd's UDP identity announcement, pointing the phone at PHONE_PORT."""
    packet = {
        "id": int(time.time() * 1000) if now_ms is None else now_ms,
        "type": "kdeconnect.identity",
        "body": {
            "deviceId": device_id,
            "deviceName": name,
            "deviceType": "laptop",
            "protocolVersion": 8,
            "tcpPort": PHONE_PORT,
            # The real capabilities follow inside TLS, from kdeconnectd itself.
            "incomingCapabilities": [],
            "outgoingCapabilities": [],
        },
    }
    return json.dumps(packet, separators=(",", ":")) + "\n"


def poke_command(packet: str) -> str:
    """Shell line run on the phone: send ``packet`` to KDE Connect's UDP port.

    toybox nc keeps waiting after stdin ends, hence ``timeout``.
    """
    return (
        f"printf %s {shlex.quote(packet)} | timeout 2 toybox nc -u 127.0.0.1 {KDECONNECT_PORT}"
        "; true"
    )


def cable_link_active(tables: Iterable[str] | None = None) -> bool:
    """True if kdeconnectd has a connection that came in through adb (from loopback)."""
    if tables is None:
        texts = []
        for name in ("/proc/net/tcp", "/proc/net/tcp6"):
            with contextlib.suppress(OSError):
                texts.append(Path(name).read_text())
        tables = texts
    port = f"{KDECONNECT_PORT:04X}"
    for text in tables:
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4 or parts[3] != _TCP_ESTABLISHED:
                continue
            local_addr, _, local_port = parts[1].rpartition(":")
            remote_addr = parts[2].rpartition(":")[0]
            if local_port == port and local_addr in _LOOPBACK and remote_addr in _LOOPBACK:
                return True
    return False


class UsbLink(QObject):
    """Keeps poking USB-connected phones while a paired phone is out of reach."""

    def __init__(
        self,
        backend: PhoneBackend,
        runner: RunnerLike,
        *,
        adb: str | None = None,
        poll_ms: int = POLL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._runner = backend, runner
        self._adb = adb if adb is not None else shutil.which("adb")
        self._identity: tuple[str, str] | None = None
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        if not self._adb:
            log.info("usb link: adb not installed")
            return
        self._backend.devicesChanged.connect(self.check)
        self._backend.deviceChanged.connect(lambda _id: self.check())
        self._timer.start()

    def needed(self) -> bool:
        return any(d.trusted and not d.reachable for d in self._backend.devices())

    def check(self) -> None:
        if self._busy or not self._adb or not self.needed():
            return
        if self._identity is None:
            self._busy = True
            self._backend.self_identity(self._got_identity)
            return
        self._busy = True
        self._runner.run(self._adb, ["devices", "-l"], self._got_devices, 10_000)

    def _got_identity(self, device_id: str, name: str) -> None:
        self._busy = False
        if device_id:
            self._identity = (device_id, name)
            self.check()

    def _got_devices(self, code: int, out: str, _err: str) -> None:
        serials = [
            d.serial
            for d in (parse_adb_devices(out) if code == 0 else [])
            if d.usable and not d.wireless
        ]
        if not serials or self._identity is None or self._adb is None:
            self._busy = False
            return
        log.info("usb link: offering KDE Connect to %d USB device(s)", len(serials))
        pending = len(serials)
        packet = identity_packet(*self._identity)
        adb = self._adb

        def finished(_code: int, _out: str, _err: str) -> None:
            nonlocal pending
            pending -= 1
            if pending == 0:
                self._busy = False

        for serial in serials:

            def reversed_(code: int, _out: str, _err: str, serial: str = serial) -> None:
                if code != 0:
                    log.info("usb link: adb reverse failed")
                    finished(code, "", "")
                    return
                self._runner.run(adb, ["-s", serial, "shell", poke_command(packet)], finished)

            self._runner.run(
                adb,
                ["-s", serial, "reverse", f"tcp:{PHONE_PORT}", f"tcp:{KDECONNECT_PORT}"],
                reversed_,
                10_000,
            )
