"""A fake scrcpy-server over a socket pair, streaming real H.264 made with libx264.

Speaks the v5.0.1 stream format (device name, codec id, session + media headers) and
records everything Mooring writes to the control socket.
"""

from __future__ import annotations

import contextlib
import socket
import struct
import threading
from fractions import Fraction

import av

from mooring.backend import scrcpy_protocol as sp
from mooring.backend.scrcpy_client import Transport


def h264_packets(count: int = 5, width: int = 64, height: int = 128) -> list[tuple[bytes, bool]]:
    """(packet bytes, is_key_frame) for ``count`` synthetic frames, Annex-B with inline SPS/PPS."""
    ctx = av.CodecContext.create("libx264", "w")
    ctx.width, ctx.height, ctx.pix_fmt = width, height, "yuv420p"
    ctx.time_base = Fraction(1, 30)
    ctx.options = {"tune": "zerolatency", "preset": "ultrafast"}
    out: list[tuple[bytes, bool]] = []
    for i in range(count):
        frame = av.VideoFrame(width, height, "yuv420p")
        for plane, value in zip(frame.planes, ((40 + i * 30) % 256, 128, 128), strict=True):
            plane.update(bytes([value]) * plane.buffer_size)
        frame.pts = i
        out += [(bytes(p), p.is_keyframe) for p in ctx.encode(frame)]
    out += [(bytes(p), p.is_keyframe) for p in ctx.encode(None)]
    return out


class FakeScrcpyServer:
    def __init__(
        self,
        *,
        name: str = "Fake Phone",
        width: int = 64,
        height: int = 128,
        frames: int = 5,
        codec: int = sp.CODEC_H264,
        config_packet: bytes | None = None,
    ) -> None:
        self.name, self.width, self.height = name, width, height
        self.codec, self.frames, self.config_packet = codec, frames, config_packet
        self.control_bytes = bytearray()
        self._video_server, self.video_client = socket.socketpair()
        self._control_server, self.control_client = socket.socketpair()
        self._closed = threading.Event()

    def transport(self) -> Transport:
        threading.Thread(target=self._serve, daemon=True).start()
        threading.Thread(target=self._collect, daemon=True).start()
        return Transport(self.video_client, self.control_client, self.close)

    def close(self) -> None:
        self._closed.set()
        for s in (self._video_server, self._control_server):
            with contextlib.suppress(OSError):
                s.close()

    def _serve(self) -> None:
        s = self._video_server
        try:
            s.sendall(self.name.encode().ljust(sp.DEVICE_NAME_LENGTH, b"\0"))
            s.sendall(struct.pack(">I", self.codec))
            if self.codec == 0:
                return
            s.sendall(struct.pack(">III", 0x80000000, self.width, self.height))
            if self.config_packet is not None:
                s.sendall(struct.pack(">QI", sp.PACKET_FLAG_CONFIG, len(self.config_packet)))
                s.sendall(self.config_packet)
            for pts, (data, key) in enumerate(h264_packets(self.frames, self.width, self.height)):
                flags = pts | (sp.PACKET_FLAG_KEY_FRAME if key else 0)
                s.sendall(struct.pack(">QI", flags, len(data)) + data)
            self._closed.wait(10)  # stay "connected" until the client stops
        except OSError:
            pass

    def _collect(self) -> None:
        try:
            while chunk := self._control_server.recv(4096):
                self.control_bytes += chunk
        except OSError:
            pass
