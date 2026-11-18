"""Hand-painted list items: conversation rows, chat bubbles, notification cards."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

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
    PAD_H, PAD_V, MARGIN, GAP = 12, 8, 14, 3
    MAX_FRACTION = 0.7

    def __init__(self, theme: Theme, view: QListView) -> None:
        super().__init__(view)
        self._theme = theme
        self._view = view

    def _text_rect(self, option: QStyleOptionViewItem, text: str) -> tuple[QRect, int]:
        viewport = self._view.viewport()
        width = viewport.width() if viewport else 400
        max_text = max(80, int(width * self.MAX_FRACTION) - 2 * self.PAD_H)
        rect = QFontMetrics(option.font).boundingRect(
            QRect(0, 0, max_text, 100_000), Qt.TextFlag.TextWordWrap, text
        )
        return rect, width

    def _time_height(self, option: QStyleOptionViewItem) -> int:
        return QFontMetrics(_scaled(option.font, 0.8)).height()

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        rect, width = self._text_rect(option, index.data(Qt.ItemDataRole.DisplayRole) or "")
        height = rect.height() + 2 * self.PAD_V + self._time_height(option) + 3 * self.GAP
        return QSize(width - 4, height)

    def paint(self, painter: QPainter | None, option: QStyleOptionViewItem, index: _Index) -> None:
        if painter is None:
            return
        t = self._theme
        text: str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        outgoing = bool(index.data(Role.Outgoing))
        ts: datetime | None = index.data(Role.Timestamp)
        text_rect, _ = self._text_rect(option, text)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect
        bubble_w = text_rect.width() + 2 * self.PAD_H
        bubble_h = text_rect.height() + 2 * self.PAD_V
        x = r.right() - self.MARGIN - bubble_w if outgoing else r.left() + self.MARGIN
        y = r.top() + self.GAP
        bubble = QRectF(x, y, bubble_w, bubble_h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.color("accent") if outgoing else t.color("surface0"))
        painter.drawRoundedRect(bubble, 14, 14)

        painter.setFont(option.font)
        painter.setPen(t.color("crust") if outgoing else t.color("text"))
        painter.drawText(
            QRect(x + self.PAD_H, y + self.PAD_V, text_rect.width() + 1, text_rect.height()),
            Qt.TextFlag.TextWordWrap,
            text,
        )

        if ts:
            time_font = _scaled(option.font, 0.8)
            painter.setFont(time_font)
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
