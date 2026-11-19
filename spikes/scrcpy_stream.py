"""Spike: talk to the installed scrcpy-server directly and decode a few frames.

Validates the v5.0.1 protocol before building the embedded mirror.
PRIVACY: prints sizes, counts and timings only. No frames are saved or shown.

    .venv/bin/python spikes/scrcpy_stream.py [serial]
"""

from __future__ import annotations

import random
import socket
import struct
import subprocess
import sys
import time

import av

SERVER = "/usr/share/scrcpy/scrcpy-server"
REMOTE = "/data/local/tmp/scrcpy-server.jar"


def adb(serial: str, *args: str, **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["adb", "-s", serial, *args], capture_output=True, text=True, **kw)


def read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"closed after {len(buf)}/{n} bytes")
        buf += chunk
    return buf


def main() -> int:
    serial = sys.argv[1] if len(sys.argv) > 1 else adb_first()
    version = subprocess.run(
        ["scrcpy", "--version"], capture_output=True, text=True
    ).stdout.split()[1]
    scid = random.randrange(1, 1 << 31)
    sock_name = f"scrcpy_{scid:08x}"
    print(f"server version {version}, scid {scid:08x}")

    t0 = time.monotonic()
    r = adb(serial, "push", SERVER, REMOTE)
    print(f"push rc={r.returncode} ({time.monotonic() - t0:.2f}s)")
    port = int(adb(serial, "forward", "tcp:0", f"localabstract:{sock_name}").stdout.strip())
    print(f"forward -> local port assigned: {port > 0}")

    args = [
        "shell",
        f"CLASSPATH={REMOTE}",
        "app_process",
        "/",
        "com.genymobile.scrcpy.Server",
        version,
        f"scid={scid:08x}",
        "log_level=info",
        "tunnel_forward=true",
        "audio=false",
        "control=true",
        "video_codec=h264",
        "max_size=1280",
        "stay_awake=true",
        "clipboard_autosync=false",
        "cleanup=true",
    ]
    server = subprocess.Popen(
        ["adb", "-s", serial, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        video, dummy = connect_first(port)
        print(f"dummy byte: {dummy!r} after {time.monotonic() - t0:.2f}s")
        control = socket.create_connection(("127.0.0.1", port))
        name = read_exact(video, 64)
        print(
            f"device name field: 64 bytes, nul-terminated={b'\\0' in name}, "
            f"text length {name.index(b'\\0') if b'\\0' in name else 64}"
        )
        codec = read_exact(video, 4)
        print(f"codec id: {codec!r} ({struct.unpack('>I', codec)[0]:#x})")

        decoder = av.CodecContext.create("h264", "r")
        frames = sessions = configs = errors = 0
        pending_config = b""
        start = time.monotonic()
        while frames < 10 and time.monotonic() - start < 10:
            head = read_exact(video, 12)
            if head[0] & 0x80:  # session packet
                flags, w, h = struct.unpack(">III", head)
                sessions += 1
                print(f"session packet: {w}x{h} client_resize={bool(flags & 1)}")
                continue
            pts_flags, size = struct.unpack(">QI", head)
            data = read_exact(video, size)
            config = bool(pts_flags & (1 << 62))
            key = bool(pts_flags & (1 << 61))
            if config:
                configs += 1
                pending_config = data  # scrcpy merges config into the next media packet
                continue
            if pending_config:
                data, pending_config = pending_config + data, b""
            try:
                decoded = decoder.decode(av.Packet(data))
            except av.error.InvalidDataError:
                errors += 1
                continue
            for frame in decoded:
                frames += 1
                if frames == 1:
                    print(
                        f"first frame {frame.width}x{frame.height} after "
                        f"{time.monotonic() - t0:.2f}s (key={key})"
                    )
        print(
            f"decoded {frames} frames, {configs} config packet(s), {sessions} session "
            f"packet(s), {errors} decode error(s), {time.monotonic() - start:.2f}s"
        )

        # BACK_OR_SCREEN_ON is harmless-ish; send nothing that changes phone state here.
        control.close()
        video.close()
        return 0 if frames else 1
    finally:
        server.terminate()
        try:
            out, _ = server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            out = ""
        lines = [
            line for line in (out or "").splitlines() if "ERROR" in line or "Exception" in line
        ]
        print(f"server error lines: {len(lines)}")
        for line in lines[:3]:
            print("  ", line[:160])
        adb(serial, "forward", "--remove", f"tcp:{port}")


def connect_first(port: int, timeout: float = 10.0) -> tuple[socket.socket, bytes]:
    """With adb forward, connect() succeeds before the server listens and the socket is
    then closed. Retry until the dummy byte arrives: that's the real readiness signal."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.create_connection(("127.0.0.1", port))
        try:
            s.settimeout(2)
            byte = s.recv(1)
            if byte:
                s.settimeout(None)
                return s, byte
        except OSError:
            pass
        s.close()
        time.sleep(0.1)
    raise ConnectionError("server never came up")


def adb_first() -> str:
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
    return next(line.split()[0] for line in out.splitlines()[1:] if line.endswith("device"))


if __name__ == "__main__":
    sys.exit(main())
