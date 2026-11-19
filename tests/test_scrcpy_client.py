"""MirrorSession and PhoneView against a fake scrcpy server streaming real H.264."""

import struct

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QImage

from mooring.backend import scrcpy_protocol as sp
from mooring.backend.scrcpy_client import MirrorSession, Transport
from mooring.theme.catppuccin import Theme
from mooring.ui.widgets.phone_view import PhoneView
from tests.fakes.fake_scrcpy import FakeScrcpyServer


def run_session(qtbot, server, **kw):
    session = MirrorSession(server.transport, **kw)
    with qtbot.waitSignal(session.started, timeout=5000) as started:
        session.start()
    return session, started.args[0]


def test_streams_and_decodes_real_h264(qtbot):
    server = FakeScrcpyServer(width=64, height=128, frames=5)
    session, name = run_session(qtbot, server)
    assert name == "Fake Phone"
    qtbot.waitUntil(lambda: session.frames >= 5, timeout=5000)
    frame = session.latest_frame()
    assert (frame.width(), frame.height()) == (64, 128)
    assert session.video_size == (64, 128)
    with qtbot.waitSignal(session.stopped, timeout=5000) as stopped:
        session.stop()
    assert stopped.args == [""]  # stopped on purpose: no error


def test_config_packet_is_merged_into_next_media_packet(qtbot):
    seen: list[bytes] = []

    class Recorder:
        def decode(self, data: bytes) -> list[QImage]:
            seen.append(data)
            return []

    server = FakeScrcpyServer(frames=2, config_packet=b"\x00\x00\x00\x01CONFIG")
    session, _ = run_session(qtbot, server, make_decoder=Recorder)
    qtbot.waitUntil(lambda: len(seen) >= 2, timeout=5000)
    assert seen[0].startswith(b"\x00\x00\x00\x01CONFIG") and len(seen[0]) > 10
    assert not seen[1].startswith(b"\x00\x00\x00\x01CONFIG")
    session.stop()


def test_video_disabled_by_phone(qtbot):
    session = MirrorSession(FakeScrcpyServer(codec=0).transport)
    with qtbot.waitSignal(session.stopped, timeout=5000) as stopped:
        session.start()
    assert stopped.args == ["the phone disabled video"]


def test_transport_failure_is_reported(qtbot):
    def broken() -> Transport:
        raise ConnectionError("Device is locked")

    session = MirrorSession(broken)
    with qtbot.waitSignal(session.stopped, timeout=5000) as stopped:
        session.start()
    assert stopped.args == ["Device is locked"]


@pytest.fixture
def view_and_server(qtbot):
    server = FakeScrcpyServer(width=100, height=200, frames=3)
    session, _ = run_session(qtbot, server)
    qtbot.waitUntil(lambda: session.frames >= 1, timeout=5000)
    view = PhoneView(Theme())
    qtbot.addWidget(view)
    view.resize(300, 300)  # wider than the 1:2 phone: letterboxed left/right
    view.set_session(session)
    view.show()
    view.repaint()
    yield view, server
    session.stop()


def control_messages(server, qtbot, count):
    qtbot.waitUntil(lambda: len(server.control_bytes) >= count, timeout=3000)
    return bytes(server.control_bytes)


def test_mouse_click_becomes_touch_in_phone_pixels(qtbot, view_and_server):
    view, server = view_and_server
    # 100x200 picture scaled 1.5x to 150x300 inside a 300x300 widget, x offset 75
    assert view.target_rect(100, 200) == QRectF(75, 0, 150, 300)
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(150, 75))
    data = control_messages(server, qtbot, 64)
    down = struct.unpack(">BBQiiHHHII", data[:32])
    up = struct.unpack(">BBQiiHHHII", data[32:64])
    assert down[:2] == (sp.MsgType.INJECT_TOUCH_EVENT, sp.MotionAction.DOWN)
    assert down[3:7] == (50, 50, 100, 200)  # x, y on the phone, current video size
    assert up[1] == sp.MotionAction.UP and up[7] == 0  # no pressure on release


def test_clicks_outside_the_picture_are_ignored(qtbot, view_and_server):
    view, server = view_and_server
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(20, 50))  # letterbox
    qtbot.wait(100)
    assert server.control_bytes == b""
    assert view.to_device(QPointF(20, 50)) is None


def test_typing_and_special_keys(qtbot, view_and_server):
    view, server = view_and_server
    view.setFocus()
    qtbot.keyClicks(view, "hi")
    qtbot.keyClick(view, Qt.Key.Key_Backspace)
    data = control_messages(server, qtbot, 6 + 6 + 28)
    assert data[:6] == sp.text("h") and data[6:12] == sp.text("i")
    assert data[12:26] == sp.keycode(sp.KeyAction.DOWN, sp.Keycode.DEL)
    assert data[26:40] == sp.keycode(sp.KeyAction.UP, sp.Keycode.DEL)


def test_right_click_is_back(qtbot, view_and_server):
    view, server = view_and_server
    qtbot.mouseClick(view, Qt.MouseButton.RightButton, pos=QPoint(150, 50))
    assert control_messages(server, qtbot, 4) == bytes([4, 0, 4, 1])
