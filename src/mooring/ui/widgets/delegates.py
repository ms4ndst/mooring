"""Hand-painted list items: conversation rows, chat bubbles, notification cards."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from mooring.backend.base import Message
from mooring.models.roles import Role
from mooring.theme.catppuccin import AVATAR_COLORS, Theme
from mooring.ui.format import initials, short_time

_Index = QModelIndex | QPersistentModelIndex


def _scaled(font: QFont, factor: float, bold: bool = False) -> QFont:
    f = QFont(font)
    f.setPointSizeF(font.pointSizeF() * factor)
    f.setBold(bold)
    return f


def _hover_background(painter: QPainter, option: QStyleOptionViewItem, theme: Theme) -> QRectF:
    rect = QRectF(option.rect.adjusted(6, 2, -6, -2))
    state = option.state
    if state & QStyle.StateFlag.State_Selected:
        bg = theme.color("surface1")
    elif state & QStyle.StateFlag.State_MouseOver:
        bg = theme.color("surface0")
    else:
        return rect
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg)
    painter.drawRoundedRect(rect, 10, 10)
    return rect


class ConversationDelegate(QStyledItemDelegate):
    HEIGHT = 64

    def __init__(self, theme: Theme, parent: QListView | None = None) -> None:
        super().__init__(parent)
        self._theme = theme

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        return QSize(200, self.HEIGHT)

    def paint(self, painter: QPainter | None, option: QStyleOptionViewItem, index: _Index) -> None:
        if painter is None:
            return
        t = self._theme
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = _hover_background(painter, option, t)

        name: str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        thread_id: int = index.data(Role.ThreadId) or 0
        unread: bool = bool(index.data(Role.Unread))
        ts: datetime | None = index.data(Role.Timestamp)
        snippet: str = index.data(Role.Snippet) or ""
        if index.data(Role.Outgoing):
            snippet = f"You: {snippet}"

        # Avatar
        av = QRectF(r.left() + 10, r.center().y() - 18, 36, 36)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.color(AVATAR_COLORS[thread_id % len(AVATAR_COLORS)]))
        painter.drawEllipse(av)
        painter.setPen(t.color("crust"))
        painter.setFont(_scaled(option.font, 0.95, bold=True))
        painter.drawText(av, Qt.AlignmentFlag.AlignCenter, initials(name))

        left = int(av.right()) + 12
        right = int(r.right()) - 12
        top = int(r.top()) + 10

        # Time, top right
        time_font = _scaled(option.font, 0.85)
        time_str = short_time(ts) if ts else ""
        time_w = QFontMetrics(time_font).horizontalAdvance(time_str)
        painter.setFont(time_font)
        painter.setPen(t.color("accent") if unread else t.color("overlay1"))
        painter.drawText(
            QRect(right - time_w, top, time_w, 20), Qt.AlignmentFlag.AlignRight, time_str
        )

        # Name
        name_font = _scaled(option.font, 1.0, bold=unread)
        if not unread:
            name_font.setWeight(QFont.Weight.Medium)
        painter.setFont(name_font)
        painter.setPen(t.color("text"))
        name_w = right - left - time_w - 8
        painter.drawText(
            QRect(left, top, name_w, 22),
            Qt.AlignmentFlag.AlignLeft,
            QFontMetrics(name_font).elidedText(name, Qt.TextElideMode.ElideRight, name_w),
        )

        # Snippet + unread dot
        snippet_font = _scaled(option.font, 0.92)
        painter.setFont(snippet_font)
        painter.setPen(t.color("text") if unread else t.color("subtext0"))
        snip_w = right - left - (18 if unread else 0)
        painter.drawText(
            QRect(left, top + 22, snip_w, 20),
            Qt.AlignmentFlag.AlignLeft,
            QFontMetrics(snippet_font).elidedText(
                snippet.replace("\n", " "), Qt.TextElideMode.ElideRight, snip_w
            ),
        )
        if unread:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.color("accent"))
            painter.drawEllipse(QRectF(right - 8, top + 28, 8, 8))
        painter.restore()


class BubbleDelegate(QStyledItemDelegate):
    """Chat bubble: optional MMS thumbnail on top, wrapped text below, time underneath."""

    PAD_H, PAD_V, MARGIN, GAP = 12, 8, 14, 3
    MAX_FRACTION = 0.7
    IMAGE_MAX = 240

    def __init__(self, theme: Theme, view: QListView) -> None:
        super().__init__(view)
        self._theme = theme
        self._view = view
        self._images: dict[tuple[int, int], QImage | None] = {}

    def _image(self, msg: Message | None) -> QImage | None:
        if msg is None:
            return None
        att = next((a for a in msg.attachments if a.is_image and a.thumbnail), None)
        if att is None:
            return None
        key = (msg.id, att.part_id)
        if key not in self._images:
            img = QImage.fromData(att.thumbnail)
            if img.isNull():
                self._images[key] = None
            else:
                self._images[key] = img.scaled(
                    self.IMAGE_MAX,
                    self.IMAGE_MAX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        return self._images[key]

    def _layout(self, option: QStyleOptionViewItem, index: _Index) -> tuple[QRect, QSize, int]:
        """(text rect, image size, viewport width). Empty text -> empty rect."""
        viewport = self._view.viewport()
        width = viewport.width() if viewport else 400
        max_text = max(80, int(width * self.MAX_FRACTION) - 2 * self.PAD_H)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        text_rect = (
            QFontMetrics(option.font).boundingRect(
                QRect(0, 0, max_text, 100_000), Qt.TextFlag.TextWordWrap, text
            )
            if text
            else QRect()
        )
        img = self._image(index.data(Role.Item))
        img_size = img.size() if img is not None else QSize(0, 0)
        return text_rect, img_size, width

    def _time_height(self, option: QStyleOptionViewItem) -> int:
        return QFontMetrics(_scaled(option.font, 0.8)).height()

    def _bubble_size(self, text_rect: QRect, img: QSize) -> QSize:
        inner_w = max(text_rect.width(), img.width())
        gap = self.GAP * 2 if img.height() and text_rect.height() else 0
        inner_h = img.height() + gap + text_rect.height()
        return QSize(inner_w + 2 * self.PAD_H, inner_h + 2 * self.PAD_V)

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        text_rect, img, width = self._layout(option, index)
        bubble = self._bubble_size(text_rect, img)
        return QSize(width - 4, bubble.height() + self._time_height(option) + 3 * self.GAP)

    def paint(self, painter: QPainter | None, option: QStyleOptionViewItem, index: _Index) -> None:
        if painter is None:
            return
        t = self._theme
        text: str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        outgoing = bool(index.data(Role.Outgoing))
        ts: datetime | None = index.data(Role.Timestamp)
        text_rect, img_size, _ = self._layout(option, index)
        size = self._bubble_size(text_rect, img_size)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect
        x = r.right() - self.MARGIN - size.width() if outgoing else r.left() + self.MARGIN
        y = r.top() + self.GAP
        bubble = QRectF(x, y, size.width(), size.height())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.color("accent") if outgoing else t.color("surface0"))
        painter.drawRoundedRect(bubble, 14, 14)

        cy = y + self.PAD_V
        img = self._image(index.data(Role.Item))
        if img is not None:
            painter.drawImage(QRect(x + self.PAD_H, cy, img.width(), img.height()), img)
            cy += img.height() + (self.GAP * 2 if text else 0)
        if text:
            painter.setFont(option.font)
            painter.setPen(t.color("crust") if outgoing else t.color("text"))
            painter.drawText(
                QRect(x + self.PAD_H, cy, text_rect.width() + 1, text_rect.height()),
                Qt.TextFlag.TextWordWrap,
                text,
            )

        if ts:
            painter.setFont(_scaled(option.font, 0.8))
            painter.setPen(t.color("overlay1"))
            align = Qt.AlignmentFlag.AlignRight if outgoing else Qt.AlignmentFlag.AlignLeft
            painter.drawText(
                QRect(
                    r.left() + self.MARGIN + 4,
                    int(bubble.bottom()) + self.GAP,
                    r.width() - 2 * self.MARGIN - 8,
                    self._time_height(option),
                ),
                align,
                ts.strftime("%Y-%m-%d %H:%M"),
            )
        painter.restore()


class NotificationDelegate(QStyledItemDelegate):
    HEIGHT = 78

    def __init__(self, theme: Theme, parent: QListView | None = None) -> None:
        super().__init__(parent)
        self._theme = theme

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        return QSize(200, self.HEIGHT)

    def paint(self, painter: QPainter | None, option: QStyleOptionViewItem, index: _Index) -> None:
        if painter is None:
            return
        t = self._theme
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = _hover_background(painter, option, t)
        if not option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.color("mantle"))
            painter.drawRoundedRect(r, 10, 10)

        left, right, top = int(r.left()) + 14, int(r.right()) - 14, int(r.top()) + 8
        width = right - left

        small = _scaled(option.font, 0.85, bold=True)
        painter.setFont(small)
        painter.setPen(t.color("accent"))
        painter.drawText(
            QRect(left, top, width, 18), Qt.AlignmentFlag.AlignLeft, index.data(Role.AppName) or ""
        )
        ts: datetime | None = index.data(Role.Timestamp)
        painter.setPen(t.color("overlay1"))
        painter.setFont(_scaled(option.font, 0.85))
        painter.drawText(
            QRect(left, top, width, 18), Qt.AlignmentFlag.AlignRight, short_time(ts) if ts else ""
        )

        title_font = _scaled(option.font, 1.0, bold=True)
        painter.setFont(title_font)
        painter.setPen(t.color("text"))
        painter.drawText(
            QRect(left, top + 20, width, 22),
            Qt.AlignmentFlag.AlignLeft,
            QFontMetrics(title_font).elidedText(
                index.data(Role.Title) or "", Qt.TextElideMode.ElideRight, width
            ),
        )
        body_font = _scaled(option.font, 0.95)
        painter.setFont(body_font)
        painter.setPen(t.color("subtext0"))
        painter.drawText(
            QRect(left, top + 42, width, 20),
            Qt.AlignmentFlag.AlignLeft,
            QFontMetrics(body_font).elidedText(
                (index.data(Qt.ItemDataRole.DisplayRole) or "").replace("\n", " "),
                Qt.TextElideMode.ElideRight,
                width,
            ),
        )
        painter.restore()


class PhotoDelegate(QStyledItemDelegate):
    """Square, centre-cropped thumbnail tile; placeholder until the thumbnail exists."""

    TILE = 160
    GAP = 8

    def __init__(self, theme: Theme, parent: QListView | None = None) -> None:
        super().__init__(parent)
        self._theme = theme

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        return QSize(self.TILE + self.GAP, self.TILE + self.GAP)

    def paint(self, painter: QPainter | None, option: QStyleOptionViewItem, index: _Index) -> None:
        if painter is None:
            return
        t = self._theme
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = option.rect
        tile = QRectF(r.x() + self.GAP / 2, r.y() + self.GAP / 2, self.TILE, self.TILE)
        clip = QPainterPath()
        clip.addRoundedRect(tile, 10, 10)
        painter.setClipPath(clip)

        pix: QPixmap | None = index.data(Qt.ItemDataRole.DecorationRole)
        if pix is not None and not pix.isNull():
            side = min(pix.width(), pix.height())
            src = QRectF((pix.width() - side) / 2, (pix.height() - side) / 2, side, side)
            painter.drawPixmap(tile, pix, src)
        else:
            painter.fillRect(tile, t.color("surface0"))
            painter.setPen(t.color("overlay0"))
            painter.drawText(tile, Qt.AlignmentFlag.AlignCenter, "…")

        painter.setClipping(False)
        if option.state & QStyle.StateFlag.State_Selected:
            pen = QPen(t.color("accent"), 3)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(tile.adjusted(1.5, 1.5, -1.5, -1.5), 10, 10)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(tile, QColor(255, 255, 255, 18))
        painter.restore()
