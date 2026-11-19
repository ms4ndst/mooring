"""Embedded screen mirror: Mooring talks to scrcpy-server itself (no scrcpy window).

Wayland can't embed another app's window, so instead of running the scrcpy client we
start the server that scrcpy ships (/usr/share/scrcpy/scrcpy-server) on the phone,
read its H.264 stream, decode it with PyAV and hand QImages to a widget. Input goes
back over the control socket (scrcpy_protocol.py).

Threads: one worker per session reads + decodes video; one drains the control socket
(device messages). The GUI only pulls the *latest* frame, so slow painting drops
frames instead of queueing them.

Privacy: frames are never written anywhere. Logs carry sizes, counts and errors only.
"""

from __future__ import annotations

import contextlib
import logging
import random
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

from mooring.backend import scrcpy_protocol as sp

log = logging.getLogger(__name__)

SERVER_PATHS = ("/usr/share/scrcpy/scrcpy-server", "/usr/local/share/scrcpy/scrcpy-server")
REMOTE_PATH = "/data/local/tmp/scrcpy-server.jar"
CONNECT_TIMEOUT = 10.0


def find_server() -> str | None:
    return next((p for p in SERVER_PATHS if Path(p).is_file()), None)


def read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("stream closed")
        buf += chunk
    return bytes(buf)


# --- transport ---------------------------------------------------------------


@dataclass
class Transport:
    """Connected sockets plus whatever must be cleaned up afterwards."""

    video: socket.socket
    control: socket.socket
    close: Callable[[], None]
    server_output: Callable[[], str] = lambda: ""


class AdbTransport:
    """Push the server, ``adb forward`` a local port, start it, wait for the dummy byte."""

    def __init__(self, adb: str, serial: str, server_path: str, version: str, max_size: int):
        self.adb, self.serial, self.server_path = adb, serial, server_path
        self.version, self.max_size = version, max_size

    def _adb(self, *args: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.adb, "-s", self.serial, *args], capture_output=True, text=True, timeout=timeout
        )

    def open(self) -> Transport:
        scid = random.randrange(1, 1 << 31)
        push = self._adb("push", self.server_path, REMOTE_PATH)
        if push.returncode != 0:
            raise ConnectionError("couldn't copy scrcpy-server to the phone")
        fwd = self._adb("forward", "tcp:0", f"localabstract:{sp.socket_name(scid)}")
        if fwd.returncode != 0 or not fwd.stdout.strip().isdigit():
            raise ConnectionError("adb forward failed")
        port = int(fwd.stdout.strip())
        cmd = [
            self.adb,
            "-s",
            self.serial,
            "shell",
            f"CLASSPATH={REMOTE_PATH}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            *sp.server_args(self.version, scid, self.max_size),
        ]
        server = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
        )
        output: list[str] = []

        def drain() -> None:
            assert server.stdout is not None
            for line in server.stdout:
                output.append(line.rstrip())
                del output[:-30]

        threading.Thread(target=drain, name="scrcpy-server-log", daemon=True).start()

        def close() -> None:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
            self._adb("forward", "--remove", f"tcp:{port}", timeout=5)

        try:
            video = self._connect_first(port, server)
            control = socket.create_connection(("127.0.0.1", port), timeout=5)
            control.settimeout(None)
        except Exception:
            close()
            detail = next((ln for ln in reversed(output) if "ERROR" in ln or "rror:" in ln), "")
            raise ConnectionError(detail or "the phone's scrcpy server didn't start") from None
        return Transport(video, control, close, lambda: "\n".join(output))

    @staticmethod
    def _connect_first(port: int, server: subprocess.Popen[str]) -> socket.socket:
        """With adb forward, connect() succeeds before the server listens (and is then
        closed). The dummy byte on the first socket is the real readiness signal."""
        deadline = time.monotonic() + CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise ConnectionError("server exited")
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                s.settimeout(2)
                if s.recv(1):
                    s.settimeout(None)
                    return s
            except OSError:
                pass
            s.close()
            time.sleep(0.1)
        raise ConnectionError("timed out")


# --- decoding ----------------------------------------------------------------


#: AVColorSpace -> PyAV/swscale colourspace name (unsupported ones fall back to BT.709)
_SWS_COLORSPACE = {1: "ITU709", 4: "FCC", 5: "ITU601", 6: "ITU601", 7: "SMPTE240M"}


class H264Decoder:
    """PyAV decoder producing QImages (no numpy needed)."""

    def __init__(self) -> None:
        import av  # imported lazily: only needed when mirroring

        self._av = av
        self._ctx = av.CodecContext.create("h264", "r")

    def decode(self, data: bytes) -> list[QImage]:
        try:
            frames = self._ctx.decode(self._av.Packet(data))
        except self._av.error.InvalidDataError:
            return []
        images = []
        for frame in frames:
            # Be explicit about the source colour space: some phones tag their stream
            # with spaces swscale can't convert (seen live: 8 = YCgCo -> "Operation not
            # supported"). Screen capture is effectively BT.709, so that's the fallback.
            rgb = frame.reformat(
                format="rgb24",
                src_colorspace=_SWS_COLORSPACE.get(frame.colorspace, "ITU709"),
                dst_colorspace="ITU709",
                src_color_range="JPEG" if frame.color_range == 2 else "MPEG",
                dst_color_range="JPEG",
            )
            plane = rgb.planes[0]
            img = QImage(
                bytes(plane), rgb.width, rgb.height, plane.line_size, QImage.Format.Format_RGB888
            )
            images.append(img.copy())  # own the pixels; the buffer above is temporary
        return images


# --- session -----------------------------------------------------------------


class MirrorSession(QObject):
    """One mirroring session. Create, ``start()``, read ``latest_frame()``, ``stop()``."""

    #: Connected. Arg: device name reported by the server.
    started = pyqtSignal(str)
    #: A new frame is available via latest_frame() (emitted at most once per frame).
    frameReady = pyqtSignal()
    #: The video size changed (rotation, fold...). Args: width, height.
    sizeChanged = pyqtSignal(int, int)
    #: Ended. Arg: "" when stopped on purpose, else a short reason.
    stopped = pyqtSignal(str)

    def __init__(
        self,
        open_transport: Callable[[], Transport],
        make_decoder: Callable[[], H264Decoder] = H264Decoder,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._open, self._make_decoder = open_transport, make_decoder
        self._transport: Transport | None = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._frame: QImage | None = None
        self._size = (0, 0)
        self._stopping = False
        self._thread: threading.Thread | None = None
        self.frames = 0

    @property
    def video_size(self) -> tuple[int, int]:
        """Current stream size: the screen size touch/scroll messages must carry."""
        return self._size

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def latest_frame(self) -> QImage | None:
        with self._lock:
            return self._frame

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mirror-video", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        t = self._transport
        if t is not None:
            for s in (t.video, t.control):
                with contextlib.suppress(OSError):
                    s.shutdown(socket.SHUT_RDWR)
                s.close()

    def send(self, message: bytes) -> None:
        t = self._transport
        if t is None or self._stopping:
            return
        try:
            with self._send_lock:
                t.control.sendall(message)
        except OSError:
            pass  # the video thread notices the broken connection and reports it

    # --- worker --------------------------------------------------------------

    def _run(self) -> None:
        reason = ""
        try:
            self._transport = t = self._open()
            if self._stopping:
                return
            name = sp.parse_device_name(read_exact(t.video, sp.DEVICE_NAME_LENGTH))
            codec = int.from_bytes(read_exact(t.video, 4), "big")
            if codec == 0:
                raise ConnectionError("the phone disabled video")
            if codec != sp.CODEC_H264:
                raise ConnectionError(f"unexpected codec {codec:#x}")
            threading.Thread(target=self._drain_control, name="mirror-control", daemon=True).start()
            self.started.emit(name)
            log.info("mirror: connected")
            self._read_video(t.video, self._make_decoder())
        except (OSError, ConnectionError, ValueError) as exc:
            if not self._stopping:
                reason = str(exc) or type(exc).__name__
                log.warning("mirror: ended: %s", type(exc).__name__)
        finally:
            if self._transport is not None:
                self.stop()
                try:
                    self._transport.close()
                except Exception as exc:  # cleanup must never raise into the thread
                    log.debug("mirror cleanup: %s", type(exc).__name__)
            log.info("mirror: stopped after %d frames", self.frames)
            self.stopped.emit(reason)

    def _read_video(self, video: socket.socket, decoder: H264Decoder) -> None:
        pending_config = b""
        while not self._stopping:
            header = sp.parse_header(read_exact(video, sp.HEADER_SIZE))
            if isinstance(header, sp.SessionHeader):
                self._size = (header.width, header.height)
                self.sizeChanged.emit(header.width, header.height)
                continue
            data = read_exact(video, header.size)
            if header.config:
                pending_config = data  # merged into the next media packet
                continue
            if pending_config:
                data, pending_config = pending_config + data, b""
            for image in decoder.decode(data):
                with self._lock:
                    self._frame = image
                self.frames += 1
                self.frameReady.emit()

    def _drain_control(self) -> None:
        """Device messages (clipboard, acks) must be read or the server may block."""
        t = self._transport
        if t is None:
            return
        try:
            while t.control.recv(4096):
                pass
        except OSError:
            pass
