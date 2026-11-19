"""Byte-exact checks against scrcpy v5.0.1's own test vectors
(app/tests/test_control_msg_serialize.c) and Streamer.java's header layout."""

import struct

import pytest

from mooring.backend import scrcpy_protocol as sp


def test_type_codes_follow_the_c_enum():
    assert sp.MsgType.INJECT_TOUCH_EVENT == 2
    assert sp.MsgType.BACK_OR_SCREEN_ON == 4
    assert sp.MsgType.RESIZE_DISPLAY == 21
    assert sp.MsgType.SCAN_FILE == 22


def test_inject_keycode():
    msg = sp.keycode(sp.KeyAction.UP, sp.Keycode.ENTER, repeat=5, metastate=0x41)
    assert msg == bytes([0, 0x01, 0, 0, 0, 0x42, 0, 0, 0, 0x05, 0, 0, 0, 0x41])


def test_inject_text():
    assert sp.text("hello, world!") == bytes([1, 0, 0, 0, 0x0D]) + b"hello, world!"


def test_inject_text_is_capped():
    msg = sp.text("a" * 400)
    assert msg[1:5] == bytes([0, 0, 0x01, 0x2C]) and len(msg) == 5 + 300


def test_inject_touch_event():
    msg = sp.touch(sp.MotionAction.DOWN, 100, 200, 1080, 1920, pointer_id=0x1234567887654321)
    assert msg == bytes(
        [2, 0x00]  # noqa: RUF005 -- laid out like scrcpy's C test vector
        + [0x12, 0x34, 0x56, 0x78, 0x87, 0x65, 0x43, 0x21]
        + [0, 0, 0, 0x64, 0, 0, 0, 0xC8]
        + [0x04, 0x38, 0x07, 0x80]
        + [0xFF, 0xFF]
        + [0, 0, 0, 0x01]
        + [0, 0, 0, 0x01]
    )
    assert len(msg) == 32


def test_touch_up_releases_pressure_and_mouse_pointer():
    msg = sp.touch(sp.MotionAction.UP, 1, 2, 10, 20, pressure=0.0, buttons=0)
    assert msg[2:10] == b"\xff" * 8  # POINTER_ID_MOUSE = -1
    assert msg[22:24] == b"\x00\x00" and msg[28:32] == b"\x00\x00\x00\x00"


def test_inject_scroll_event():
    msg = sp.scroll(260, 1026, 1080, 1920, hscroll=16, vscroll=-16, buttons=1)
    assert msg == bytes(
        [3, 0, 0, 0x01, 0x04, 0, 0, 0x04, 0x02, 0x04, 0x38, 0x07, 0x80]  # noqa: RUF005
        + [0x7F, 0xFF, 0x80, 0x00, 0, 0, 0, 1]
    )


def test_back_or_screen_on_and_simple_messages():
    assert sp.back_or_screen_on(sp.KeyAction.UP) == bytes([4, 1])
    assert sp.simple(sp.MsgType.ROTATE_DEVICE) == bytes([11])
    assert sp.set_display_power(True) == bytes([10, 1])


def test_session_header():
    data = struct.pack(">III", 0x80000000 | 1, 580, 1280)
    assert sp.parse_header(data) == sp.SessionHeader(580, 1280, True)


@pytest.mark.parametrize(
    ("flags", "config", "key"),
    [
        (sp.PACKET_FLAG_CONFIG, True, False),
        (sp.PACKET_FLAG_KEY_FRAME | 123, False, True),
        (99, False, False),
    ],
)
def test_media_header(flags, config, key):
    header = sp.parse_header(struct.pack(">QI", flags, 4096))
    assert isinstance(header, sp.MediaHeader)
    assert (header.size, header.config, header.key_frame) == (4096, config, key)


def test_device_name_and_server_args():
    assert sp.parse_device_name(b"Phone\0" + b"\0" * 58) == "Phone"
    args = sp.server_args("5.0.1", 0xABC)
    assert args[0] == "5.0.1" and "scid=00000abc" in args and "tunnel_forward=true" in args
    assert sp.socket_name(0xABC) == "scrcpy_00000abc"
