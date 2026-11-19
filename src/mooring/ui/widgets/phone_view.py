"""Shows a MirrorSession's frames and turns mouse/keyboard input into phone input."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PyQt6.QtWidgets import QSizePolicy, QWidget

from mooring.backend import scrcpy_protocol as sp
from mooring.backend.scrcpy_client import MirrorSession
from mooring.theme.catppuccin import Theme

#: Qt key -> Android keycode for keys that aren't plain text.
KEYMAP: dict[int, int] = {
    Qt.Key.Key_Backspace: sp.Keycode.DEL,
    Qt.Key.Key_Delete: sp.Keycode.FORWARD_DEL,
    Qt.Key.Key_Return: sp.Keycode.ENTER,
    Qt.Key.Key_Enter: sp.Keycode.ENTER,
    Qt.Key.Key_Tab: sp.Keycode.TAB,
    Qt.Key.Key_Escape: sp.Keycode.BACK,
    Qt.Key.Key_Left: sp.Keycode.DPAD_LEFT,
    Qt.Key.Key_Right: sp.Keycode.DPAD_RIGHT,
    Qt.Key.Key_Up: sp.Keycode.DPAD_UP,
    Qt.Key.Key_Down: sp.Keycode.DPAD_DOWN,
    Qt.Key.Key_Home: sp.Keycode.MOVE_HOME,
    Qt.Key.Key_End: sp.Keycode.MOVE_END,
    Qt.Key.Key_PageUp: sp.Keycode.PAGE_UP,
    Qt.Key.Key_PageDown: sp.Keycode.PAGE_DOWN,
}


class PhoneView(QWidget):
    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._session: MirrorSession | None = None
        self._touching = False
        self.status_text = "Connecting…"
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 300)
        theme.changed.connect(self.update)

    def set_session(self, session: MirrorSession | None) -> None:
        if self._session is not None:
            self._session.frameReady.disconnect(self.update)
        self._session = session
        self._touching = False
        self.status_text = "Connecting…"
        if session is not None:
            session.frameReady.connect(self.update)
        self.update()

    # --- painting ------------------------------------------------------------

    def target_rect(self, image_w: int, image_h: int) -> QRectF:
        """Where a frame of that size is drawn: scaled to fit, centred."""
        if image_w <= 0 or image_h <= 0:
            return QRectF()
        scale = min(self.width() / image_w, self.height() / image_h)
        w, h = image_w * scale, image_h * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), self._theme.color("crust"))
        frame = self._session.latest_frame() if self._session else None
        if frame is None or frame.isNull():
            painter.setPen(self._theme.color("overlay1"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.status_text)
            return
        painter.drawImage(self.target_rect(frame.width(), frame.height()), frame)

    def _current_target(self) -> QRectF:
        """Where the current frame is (or would be) drawn. Computed, not cached from the
        last paint, so input before a repaint (or right after a resize) still maps."""
        frame = self._session.latest_frame() if self._session else None
        if frame is None or frame.isNull():
            return QRectF()
        return self.target_rect(frame.width(), frame.height())

    # --- input ---------------------------------------------------------------

    def to_device(self, pos: QPointF) -> tuple[int, int, int, int] | None:
        """Widget position -> (x, y, video_w, video_h) on the phone, or None if off-screen."""
        if self._session is None:
            return None
        target = self._current_target()
        if target.isEmpty():
            return None
        vw, vh = self._session.video_size
        if vw <= 0 or vh <= 0:
            return None
        rx = (pos.x() - target.x()) / target.width()
        ry = (pos.y() - target.y()) / target.height()
        if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0):
            return None
        return min(int(rx * vw), vw - 1), min(int(ry * vh), vh - 1), vw, vh

    def _touch(self, action: sp.MotionAction, pos: QPointF) -> bool:
        mapped = self.to_device(pos)
        if mapped is None or self._session is None:
            return False
        x, y, w, h = mapped
        up = action == sp.MotionAction.UP
        self._session.send(
            sp.touch(action, x, y, w, h, pressure=0.0 if up else 1.0, buttons=0 if up else 1)
        )
        return True

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or self._session is None:
            return
        self.setFocus()
        button = event.button()
        if button == Qt.MouseButton.LeftButton:
            self._touching = self._touch(sp.MotionAction.DOWN, event.position())
        elif button == Qt.MouseButton.RightButton:  # like scrcpy: back (or wake the screen)
            self._session.send(sp.back_or_screen_on(sp.KeyAction.DOWN))
            self._session.send(sp.back_or_screen_on(sp.KeyAction.UP))
        elif button == Qt.MouseButton.MiddleButton:
            self.press_key(sp.Keycode.HOME)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and self._touching:
            self._touch(sp.MotionAction.MOVE, event.position())

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and self._touching and event.button() == Qt.MouseButton.LeftButton:
            self._touching = False
            if not self._touch(sp.MotionAction.UP, event.position()):
                # released outside the picture: end the gesture at the nearest edge
                t = self._current_target()
                clamped = QPointF(
                    min(max(event.position().x(), t.left()), t.right() - 1),
                    min(max(event.position().y(), t.top()), t.bottom() - 1),
                )
                self._touch(sp.MotionAction.UP, clamped)

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        if event is None or self._session is None:
            return
        mapped = self.to_device(event.position())
        if mapped is None:
            return
        x, y, w, h = mapped
        delta = event.angleDelta()
        self._session.send(sp.scroll(x, y, w, h, delta.x() / 120, delta.y() / 120))

    def press_key(self, code: int) -> None:
        if self._session is not None:
            self._session.send(sp.keycode(sp.KeyAction.DOWN, code))
            self._session.send(sp.keycode(sp.KeyAction.UP, code))

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None or self._session is None:
            return
        code = KEYMAP.get(event.key())
        if code is not None:
            self._session.send(
                sp.keycode(sp.KeyAction.DOWN, code, repeat=1 if event.isAutoRepeat() else 0)
            )
            return
        typed = event.text()
        ctrl = event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        )
        if typed and typed.isprintable() and not ctrl:
            self._session.send(sp.text(typed))
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent | None) -> None:
        if event is None or self._session is None or event.isAutoRepeat():
            return
        code = KEYMAP.get(event.key())
        if code is not None:
            self._session.send(sp.keycode(sp.KeyAction.UP, code))
        else:
            super().keyReleaseEvent(event)

    def focusNextPrevChild(self, next: bool) -> bool:
        return False  # keep Tab for the phone while the view has focus
