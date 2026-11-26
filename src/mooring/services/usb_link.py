"""Reach KDE Connect over the USB cable when the phone is on another network.

KDE Connect's LAN link needs the phone and this computer on the same network. With the
phone plugged in (USB debugging on), the link can run through adb instead:

1. ``adb reverse tcp:1764 tcp:1716``: on the phone, 127.0.0.1:1764 leads to kdeconnectd.
2. Send the phone our identity packet over UDP to its own 127.0.0.1:1716, with
   ``tcpPort`` 1764. That is what kdeconnectd's broadcast looks like, so the phone
   connects back to 127.0.0.1:1764, i.e. through the cable to kdeconnectd. TLS and
   pairing then work as usual: an unpaired or spoofed peer gets nowhere.

Photos and files: kdeconnectd runs sshfs against the link's peer address, which over the
cable is 127.0.0.1. So once the phone has started its sftp server (on the first mount
request), ``forward_storage`` finds the server's port in the phone's /proc/net/tcp* and
runs ``adb forward tcp:P tcp:P``; the next mount reaches the phone. Forwards are removed
again when the cable link ends, so they don't block kdeconnectd's own ports on Wi-Fi.
File transfers (share) still need a shared network.

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
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QObject, QTimer

from mooring.backend.base import PhoneBackend
from mooring.backend.mirror import Done, parse_adb_devices

log = logging.getLogger(__name__)

KDECONNECT_PORT = 1716
PHONE_PORT = 1764
POLL_MS = 15_000
KDECONNECT_PACKAGE = "org.kde.kdeconnect_tp"
#: The phone's sftp server takes the first free port of 1739-1764 (1764 is our reverse).
SFTP_PORTS = range(1739, PHONE_PORT)
_TCP_LISTEN = "0A"

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


def storage_command() -> str:
    """Shell line run on the phone: KDE Connect's uid, then the socket tables."""
    return f"pm list packages -U {KDECONNECT_PACKAGE}; cat /proc/net/tcp /proc/net/tcp6"


def parse_sftp_ports(text: str) -> list[int]:
    """Ports in SFTP_PORTS that KDE Connect listens on, from ``storage_command`` output."""
    uid = ""
    for line in text.splitlines():
        if line.startswith(f"package:{KDECONNECT_PACKAGE} ") and "uid:" in line:
            uid = line.rsplit("uid:", 1)[1].strip().split(",")[0]
    if not uid:
        return []
    ports: set[int] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[3] != _TCP_LISTEN or parts[7] != uid:
            continue
        try:
            port = int(parts[1].rpartition(":")[2], 16)
        except ValueError:
            continue
        if port in SFTP_PORTS:
            ports.add(port)
    return sorted(ports)


def parse_storage_forwards(text: str) -> list[tuple[str, int]]:
    """``adb forward --list`` lines that look like ours: ``SERIAL tcp:P tcp:P``, P in range."""
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == parts[2] and parts[1].startswith("tcp:"):
            try:
                port = int(parts[1][4:])
            except ValueError:
                continue
            if port in SFTP_PORTS:
                found.append((parts[0], port))
    return found


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
        #: (serial, port) pairs we forwarded for the phone's sftp server
        self._forwards: set[tuple[str, int]] = set()
        #: storage requests waiting for the start-up cleanup (it would undo their forward)
        self._cleaning = False
        self._after_cleanup: list[Callable[[], None]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        if not self._adb:
            log.info("usb link: adb not installed")
            return
        if hasattr(self._backend, "forward_storage"):
            self._backend.forward_storage = self.forward_storage
        # Forwards a previous run left behind (quit while on the cable) would block these
        # ports for kdeconnectd's own transfers on Wi-Fi. The cable link re-adds its own.
        self._cleaning = True
        self._runner.run(self._adb, ["forward", "--list"], self._clear_stale_forwards, 10_000)
        self._backend.devicesChanged.connect(self.check)
        self._backend.deviceChanged.connect(lambda _id: self.check())
        self._timer.start()

    def needed(self) -> bool:
        return any(d.trusted and not d.reachable for d in self._backend.devices())

    def check(self) -> None:
        if self._forwards and self._adb and not cable_link_active():
            self._remove_forwards(self._adb)
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

    # --- photos and files over the cable --------------------------------------

    def forward_storage(self, _device_id: str, done: Callable[[bool], None]) -> None:
        """Forward the phone's sftp port(s) over adb. ``done(True)`` if any were added."""
        adb = self._adb
        if not adb:
            done(False)
            return
        if self._cleaning:
            self._after_cleanup.append(lambda: self.forward_storage(_device_id, done))
            return

        def got_devices(code: int, out: str, _err: str) -> None:
            serials = [
                d.serial
                for d in (parse_adb_devices(out) if code == 0 else [])
                if d.usable and not d.wireless
            ]
            next_serial(serials, False)

        def next_serial(serials: list[str], changed: bool) -> None:
            if not serials:
                done(changed)
                return
            serial, rest = serials[0], serials[1:]

            def got_ports(code: int, out: str, _err: str) -> None:
                ports = [
                    p
                    for p in (parse_sftp_ports(out) if code == 0 else [])
                    if (serial, p) not in self._forwards
                ]
                forward(serial, ports, rest, changed)

            self._runner.run(adb, ["-s", serial, "shell", storage_command()], got_ports, 10_000)

        def forward(serial: str, ports: list[int], rest: list[str], changed: bool) -> None:
            if not ports:
                next_serial(rest, changed)
                return
            port = ports[0]

            def forwarded(code: int, _out: str, _err: str) -> None:
                if code == 0:
                    self._forwards.add((serial, port))
                    log.info("usb link: forwarded the phone's storage port")
                else:
                    log.info("usb link: adb forward failed")
                forward(serial, ports[1:], rest, changed or code == 0)

            self._runner.run(
                adb, ["-s", serial, "forward", f"tcp:{port}", f"tcp:{port}"], forwarded, 10_000
            )

        self._runner.run(adb, ["devices", "-l"], got_devices, 10_000)

    def _clear_stale_forwards(self, code: int, out: str, _err: str) -> None:
        stale = parse_storage_forwards(out) if code == 0 and self._adb else []
        pending = len(stale)

        def removed(*_a: object) -> None:
            nonlocal pending
            pending -= 1
            if pending <= 0:
                self._cleanup_done()

        if not stale or self._adb is None:
            self._cleanup_done()
            return
        for serial, port in stale:
            self._runner.run(
                self._adb, ["-s", serial, "forward", "--remove", f"tcp:{port}"], removed
            )

    def _cleanup_done(self) -> None:
        self._cleaning = False
        waiting, self._after_cleanup = self._after_cleanup, []
        for fn in waiting:
            fn()

    def _remove_forwards(self, adb: str) -> None:
        for serial, port in sorted(self._forwards):
            self._runner.run(
                adb, ["-s", serial, "forward", "--remove", f"tcp:{port}"], lambda *_a: None
            )
        log.info("usb link: cable link gone, removed %d storage forward(s)", len(self._forwards))
        self._forwards.clear()
