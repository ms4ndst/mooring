"""Screen mirroring and phone apps via scrcpy/adb, run as QProcess children.

- ``adb devices -l`` / ``adb pair`` / ``adb connect`` for discovery and Wi-Fi pairing
- ``scrcpy --list-apps`` for launchable apps (names + packages; '*' marks system apps)
- ``scrcpy`` for the mirror window, ``scrcpy --new-display --start-app=<pkg>`` for an
  app in its own window (scrcpy 3.0+). On Wayland these are separate windows; Niri
  opens each as a new column.

Privacy: installed apps say a lot about a person. Log counts and exit codes only,
never package names, app names, serials or command output.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from PyQt6 import sip
from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from mooring.backend.pyav import pyav_available
from mooring.backend.scrcpy_client import AdbTransport, MirrorSession, find_server

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AdbDevice:
    serial: str
    state: str  # "device", "unauthorized", "offline", ...
    model: str = ""
    product: str = ""

    @property
    def usable(self) -> bool:
        return self.state == "device"

    @property
    def wireless(self) -> bool:
        return ":" in self.serial or self.serial.startswith("adb-")

    @property
    def label(self) -> str:
        how = "Wi-Fi" if self.wireless else "USB"
        name = self.model.replace("_", " ") or "Android device"
        state = "" if self.usable else f" ({self.state})"
        return f"{name} · {how}{state}"


@dataclass(frozen=True, slots=True)
class PhoneApp:
    name: str
    package: str
    system: bool


@dataclass
class Tools:
    adb: str | None = None
    scrcpy: str | None = None
    scrcpy_version: str = ""
    server: str | None = None  # scrcpy-server jar, needed for the embedded mirror

    @property
    def embedded_ready(self) -> bool:
        """Mooring can mirror inside its own window (needs PyAV and the server jar)."""
        return bool(
            self.adb and self.server and version_number(self.scrcpy_version) and pyav_available()
        )

    @property
    def ready(self) -> bool:
        return bool(self.adb and self.scrcpy)


_SCRCPY_VERSION = re.compile(r"scrcpy (\d+(?:\.\d+)+)")


def parse_adb_devices(text: str) -> list[AdbDevice]:
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("List of devices", "*")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        devices.append(
            AdbDevice(parts[0], parts[1], info.get("model", ""), info.get("product", ""))
        )
    return devices


def parse_app_list(text: str) -> list[PhoneApp]:
    """Lines like `` - App Name     com.example.app`` ('*' = system app)."""
    apps = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 4 or stripped[0] not in "*-" or stripped[1] != " ":
            continue
        rest = stripped[2:].rstrip()
        name, _, package = rest.rpartition(" ")
        if not package or "." not in package:
            continue
        apps.append(PhoneApp(name.strip() or package, package, stripped[0] == "*"))
    # your own apps first, then alphabetical
    return sorted(apps, key=lambda a: (a.system, a.name.casefold()))


def version_number(version: str) -> str:
    """'scrcpy 5.0.1 <https://...>' -> '5.0.1' ('' if unknown)."""
    m = _SCRCPY_VERSION.search(version)
    return m.group(1) if m else ""


def scrcpy_supports_apps(version: str) -> bool:
    number = version_number(version)
    if not number:
        return False
    major, minor = (int(p) for p in ([*number.split("."), "0"])[:2])
    return (major, minor) >= (3, 0)


def pick_adb_device(
    devices: list[AdbDevice], kde_name: str = "", preferred: str = ""
) -> AdbDevice | None:
    """Which ADB device is the KDE Connect phone? Saved choice, then model match, then the
    only one. KDE Connect exposes no serial, so this is a best effort."""
    usable = [d for d in devices if d.usable]
    if preferred:
        for d in usable:
            if d.serial == preferred:
                return d
    if len(usable) == 1:
        return usable[0]
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", kde_name)}
    for d in usable:
        model = re.sub(r"[^a-z0-9]", "", d.model.lower())
        if model and any(model in w or w.startswith(model) for w in words):
            return d
    return usable[0] if usable else None


# --- process plumbing (swappable in tests) ---------------------------------------

Done = Callable[[int, str, str], None]  # exit code, stdout, stderr


class Runner(QObject):
    """Starts processes. ``run`` collects output; ``spawn`` is for long-lived windows."""

    def run(self, program: str, args: list[str], done: Done, timeout_ms: int = 30_000) -> None:
        proc = QProcess(self)

        def finished(code: int, _status: QProcess.ExitStatus) -> None:
            if sip.isdeleted(proc) or sip.isdeleted(self):
                return  # killed because its owner is being destroyed; nobody to tell
            out = bytes(proc.readAllStandardOutput().data()).decode(errors="replace")
            err = bytes(proc.readAllStandardError().data()).decode(errors="replace")
            proc.deleteLater()
            done(code, out, err)

        def failed(error: QProcess.ProcessError) -> None:
            if error == QProcess.ProcessError.FailedToStart:
                proc.deleteLater()
                done(-1, "", f"{program} could not be started")

        proc.finished.connect(finished)
        proc.errorOccurred.connect(failed)
        proc.start(program, args)

    def spawn(self, program: str, args: list[str], done: Done) -> QProcess:
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        tail: list[str] = []

        def read() -> None:
            if sip.isdeleted(proc):
                return
            text = bytes(proc.readAll().data()).decode(errors="replace")
            tail.extend(text.splitlines())
            del tail[:-20]

        def finished(code: int, _status: QProcess.ExitStatus) -> None:
            if sip.isdeleted(proc) or sip.isdeleted(self):
                return
            read()
            done(code, "", "\n".join(tail))
            proc.deleteLater()

        def failed(error: QProcess.ProcessError) -> None:
            if error == QProcess.ProcessError.FailedToStart:
                done(-1, "", f"{program} could not be started")
                proc.deleteLater()

        proc.readyRead.connect(read)
        proc.finished.connect(finished)
        proc.errorOccurred.connect(failed)
        proc.start(program, args)
        return proc


def _last_error(output: str) -> str:
    """The most useful line of scrcpy/adb output, for the status bar."""
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith(("ERROR:", "error:", "adb: ")) or "failed" in line.lower():
            return re.sub(r"^(ERROR|error|adb):\s*", "", line)
    return output.strip().splitlines()[-1] if output.strip() else ""


@dataclass
class _Window:
    proc: QProcess
    serial: str
    title: str
    package: str = ""
    started: bool = field(default=False)


class MirrorBackend(QObject):
    devicesChanged = pyqtSignal()
    appsLoaded = pyqtSignal(str, list)  # serial, list[PhoneApp]
    #: Number of scrcpy windows currently open.
    windowsChanged = pyqtSignal(int)
    #: Wi-Fi pairing result. Args: success, short message.
    paired = pyqtSignal(bool, str)
    error = pyqtSignal(str)

    def __init__(self, runner: Runner | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = runner or Runner(self)
        self.tools = Tools()
        self._devices: list[AdbDevice] = []
        self._windows: list[_Window] = []
        #: Replaces how sessions are made (tests use a fake server).
        self.session_factory: Callable[[str], MirrorSession] | None = None

    def _alive(self, fn: Done) -> Done:
        """Drop callbacks that arrive after this backend was destroyed (e.g. app quit)."""

        def guarded(code: int, out: str, err: str) -> None:
            if not sip.isdeleted(self):
                fn(code, out, err)

        return guarded

    # --- discovery -----------------------------------------------------------

    def detect_tools(self, done: Callable[[Tools], None] | None = None) -> None:
        self.tools = Tools(shutil.which("adb"), shutil.which("scrcpy"), server=find_server())
        if not self.tools.scrcpy:
            if done:
                done(self.tools)
            return

        def got(_code: int, out: str, err: str) -> None:
            m = _SCRCPY_VERSION.search(out + err)
            self.tools.scrcpy_version = m.group(0) if m else ""  # e.g. "scrcpy 5.0.1"
            if done:
                done(self.tools)

        self._runner.run(self.tools.scrcpy, ["--version"], self._alive(got))

    def devices(self) -> list[AdbDevice]:
        return list(self._devices)

    def refresh_devices(self) -> None:
        if not self.tools.adb:
            self._devices = []
            self.devicesChanged.emit()
            return

        def got(code: int, out: str, _err: str) -> None:
            self._devices = parse_adb_devices(out) if code == 0 else []
            log.info("adb: %d device(s)", len(self._devices))
            self.devicesChanged.emit()

        self._runner.run(self.tools.adb, ["devices", "-l"], self._alive(got))

    def pair(self, pair_address: str, code: str, connect_address: str = "") -> None:
        """Wireless debugging: ``adb pair host:port code``, then ``adb connect``."""
        if not self.tools.adb:
            self.paired.emit(False, "adb is not installed")
            return
        adb = self.tools.adb

        def connected(rc: int, out: str, err: str) -> None:
            ok = rc == 0 and "connected" in out.lower() and "cannot" not in out.lower()
            self.paired.emit(ok, "Connected" if ok else _last_error(out + err))
            self.refresh_devices()

        def paired(rc: int, out: str, err: str) -> None:
            ok = rc == 0 and "successfully paired" in out.lower()
            if not ok:
                self.paired.emit(False, _last_error(out + err) or "Pairing failed")
                return
            log.info("adb: paired over Wi-Fi")
            if connect_address:
                self._runner.run(adb, ["connect", connect_address], self._alive(connected))
            else:  # adb usually connects by itself via mDNS shortly after pairing
                self.paired.emit(True, "Paired")
                self.refresh_devices()

        self._runner.run(adb, ["pair", pair_address, code], self._alive(paired))

    # --- apps ----------------------------------------------------------------

    def load_apps(self, serial: str) -> None:
        if not self.tools.scrcpy:
            return

        def got(code: int, out: str, err: str) -> None:
            if code != 0:
                self.error.emit(_last_error(out + err) or "Couldn't list the phone's apps")
                return
            apps = parse_app_list(out + "\n" + err)
            log.info("apps: %d launchable", len(apps))
            self.appsLoaded.emit(serial, apps)

        self._runner.run(self.tools.scrcpy, ["-s", serial, "--list-apps"], self._alive(got), 60_000)

    # --- windows -------------------------------------------------------------

    def window_count(self) -> int:
        return len(self._windows)

    def mirror(self, serial: str, title: str) -> None:
        self._spawn(serial, title, ["--window-title", title, "--stay-awake"])

    def launch_app(self, serial: str, app: PhoneApp) -> None:
        if not scrcpy_supports_apps(self.tools.scrcpy_version):
            self.error.emit("Opening single apps needs scrcpy 3.0 or newer")
            return
        self._spawn(
            serial,
            app.name,
            ["--new-display", f"--start-app={app.package}", "--window-title", app.name],
            package=app.package,
        )

    def _spawn(self, serial: str, title: str, extra: list[str], package: str = "") -> None:
        if not self.tools.scrcpy:
            self.error.emit("scrcpy is not installed")
            return
        window: _Window

        def done(code: int, _out: str, tail: str) -> None:
            if window in self._windows:
                self._windows.remove(window)
            log.info("scrcpy window closed (exit %d)", code)
            # 0 = closed by the user; 2 = device disconnected
            if code not in (0, 2):
                self.error.emit(f"scrcpy: {_last_error(tail) or f'exited with code {code}'}")
            self.windowsChanged.emit(len(self._windows))

        proc = self._runner.spawn(self.tools.scrcpy, ["-s", serial, *extra], self._alive(done))
        window = _Window(proc, serial, title, package)
        self._windows.append(window)
        log.info("scrcpy window opened (%d open)", len(self._windows))
        self.windowsChanged.emit(len(self._windows))

    # --- embedded mirror -----------------------------------------------------

    def new_session(self, serial: str, max_size: int = 1600) -> MirrorSession | None:
        """A MirrorSession for the phone, or None (and an error) if it can't be done."""
        if self.session_factory is not None:
            return self.session_factory(serial)
        tools = self.tools
        if not tools.embedded_ready or tools.adb is None or tools.server is None:
            self.error.emit("Mirroring in Mooring needs adb, scrcpy (with scrcpy-server) and PyAV")
            return None
        adb, server, version = tools.adb, tools.server, version_number(tools.scrcpy_version)
        return MirrorSession(
            lambda: AdbTransport(adb, serial, server, version, max_size).open(), parent=self
        )

    def close_all(self) -> None:
        for w in list(self._windows):
            w.proc.terminate()
