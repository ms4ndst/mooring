"""scrcpy client/server wire protocol (v5.0.1), as pure encode/decode functions.

Sources: scrcpy v5.0.1 doc/develop.md, app/src/control_msg.h, app/tests/
test_control_msg_serialize.c (byte-exact vectors, reused in our tests) and
server/.../device/Streamer.java. Everything is big-endian.

Video socket (tunnel_forward=true): 1 dummy byte, 64-byte device name, 4-byte codec id,
then 12-byte headers. If the top bit of the first header byte is set it's a session
packet (flags u32, width u32, height u32, sent at start and on every resize);
otherwise it's a media packet: pts_and_flags u64 (bit 62 config, bit 61 key frame),
size u32, then ``size`` bytes. Config packets (SPS/PPS) are merged into the next
media packet before decoding, as scrcpy's own client does.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

DEVICE_NAME_LENGTH = 64
CODEC_H264 = 0x68323634
HEADER_SIZE = 12

PACKET_FLAG_SESSION = 1 << 63
PACKET_FLAG_CONFIG = 1 << 62
PACKET_FLAG_KEY_FRAME = 1 << 61
PTS_MASK = PACKET_FLAG_KEY_FRAME - 1

TEXT_MAX_LENGTH = 300

POINTER_ID_MOUSE = (1 << 64) - 1  # UINT64_C(-1)
POINTER_ID_GENERIC_FINGER = (1 << 64) - 2


class MsgType(IntEnum):
    """enum sc_control_msg_type, in declaration order."""

    INJECT_KEYCODE = 0
    INJECT_TEXT = 1
    INJECT_TOUCH_EVENT = 2
    INJECT_SCROLL_EVENT = 3
    BACK_OR_SCREEN_ON = 4
    EXPAND_NOTIFICATION_PANEL = 5
    EXPAND_SETTINGS_PANEL = 6
    COLLAPSE_PANELS = 7
    GET_CLIPBOARD = 8
    SET_CLIPBOARD = 9
    SET_DISPLAY_POWER = 10
    ROTATE_DEVICE = 11
    UHID_CREATE = 12
    UHID_INPUT = 13
    UHID_DESTROY = 14
    OPEN_HARD_KEYBOARD_SETTINGS = 15
    START_APP = 16
    RESET_VIDEO = 17
    CAMERA_SET_TORCH = 18
    CAMERA_ZOOM_IN = 19
    CAMERA_ZOOM_OUT = 20
    RESIZE_DISPLAY = 21
    SCAN_FILE = 22


class KeyAction(IntEnum):
    DOWN = 0
    UP = 1


class MotionAction(IntEnum):
    DOWN = 0
    UP = 1
    MOVE = 2


BUTTON_PRIMARY = 1


class Keycode(IntEnum):
    """The Android keycodes Mooring sends (android/keycodes.h)."""

    HOME = 3
    BACK = 4
    DPAD_UP = 19
    DPAD_DOWN = 20
    DPAD_LEFT = 21
    DPAD_RIGHT = 22
    VOLUME_UP = 24
    VOLUME_DOWN = 25
    POWER = 26
    TAB = 61
    SPACE = 62
    ENTER = 66
    DEL = 67  # backspace
    ESCAPE = 111
    FORWARD_DEL = 112
    MOVE_HOME = 122
    MOVE_END = 123
    PAGE_UP = 92
    PAGE_DOWN = 93
    APP_SWITCH = 187


# --- video stream ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionHeader:
    width: int
    height: int
    client_resize: bool


@dataclass(frozen=True, slots=True)
class MediaHeader:
    pts: int
    size: int
    config: bool
    key_frame: bool


def parse_header(data: bytes) -> SessionHeader | MediaHeader:
    if len(data) != HEADER_SIZE:
        raise ValueError(f"header must be {HEADER_SIZE} bytes")
    if data[0] & 0x80:
        flags, width, height = struct.unpack(">III", data)
        return SessionHeader(width, height, bool(flags & 1))
    pts_flags, size = struct.unpack(">QI", data)
    return MediaHeader(
        pts=pts_flags & PTS_MASK,
        size=size,
        config=bool(pts_flags & PACKET_FLAG_CONFIG),
        key_frame=bool(pts_flags & PACKET_FLAG_KEY_FRAME),
    )


def parse_device_name(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


# --- control messages --------------------------------------------------------


def keycode(action: KeyAction, code: int, repeat: int = 0, metastate: int = 0) -> bytes:
    return struct.pack(">BBIII", MsgType.INJECT_KEYCODE, action, code, repeat, metastate)


def text(value: str) -> bytes:
    raw = value.encode("utf-8")[:TEXT_MAX_LENGTH]
    return struct.pack(">BI", MsgType.INJECT_TEXT, len(raw)) + raw


def _pressure(value: float) -> int:
    """float [0, 1] -> u16 fixed point (1.0 = 0xffff)."""
    v = min(max(value, 0.0), 1.0)
    return 0xFFFF if v >= 1.0 else int(v * 0x10000)


def touch(
    action: MotionAction,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    pointer_id: int = POINTER_ID_MOUSE,
    pressure: float = 1.0,
    action_button: int = BUTTON_PRIMARY,
    buttons: int = BUTTON_PRIMARY,
) -> bytes:
    """``width``/``height`` must be the current video size (the server rejects stale sizes)."""
    return struct.pack(
        ">BBQiiHHHII",
        MsgType.INJECT_TOUCH_EVENT,
        action,
        pointer_id,
        x,
        y,
        width,
        height,
        _pressure(pressure),
        action_button,
        buttons,
    )


def _scroll_value(v: float) -> int:
    """float in [-16, 16] -> i16 fixed point (16 = 0x7fff, -16 = -0x8000)."""
    v = min(max(v / 16.0, -1.0), 1.0)
    return 0x7FFF if v >= 1.0 else int(v * 0x8000)


def scroll(
    x: int, y: int, width: int, height: int, hscroll: float, vscroll: float, buttons: int = 0
) -> bytes:
    return struct.pack(
        ">BiiHHhhI",
        MsgType.INJECT_SCROLL_EVENT,
        x,
        y,
        width,
        height,
        _scroll_value(hscroll),
        _scroll_value(vscroll),
        buttons,
    )


def back_or_screen_on(action: KeyAction) -> bytes:
    return struct.pack(">BB", MsgType.BACK_OR_SCREEN_ON, action)


def simple(kind: MsgType) -> bytes:
    """Messages without payload (panels, rotate, reset video...)."""
    return struct.pack(">B", kind)


def set_display_power(on: bool) -> bytes:
    return struct.pack(">BB", MsgType.SET_DISPLAY_POWER, 1 if on else 0)


def server_args(version: str, scid: int, max_size: int = 1600) -> list[str]:
    """Arguments for ``app_process / com.genymobile.scrcpy.Server``."""
    return [
        version,  # must equal the server's version exactly
        f"scid={scid:08x}",
        "log_level=info",
        "tunnel_forward=true",
        "audio=false",
        "control=true",
        "video_codec=h264",
        f"max_size={max_size}",
        "stay_awake=true",
        "clipboard_autosync=false",
        "cleanup=true",
    ]


def socket_name(scid: int) -> str:
    return f"scrcpy_{scid:08x}"
