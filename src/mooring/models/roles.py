from enum import IntEnum

from PyQt6.QtCore import Qt

_BASE = Qt.ItemDataRole.UserRole


class Role(IntEnum):
    """Custom item-data roles shared by every Mooring list model."""

    Item = _BASE + 1  # the underlying dataclass
    ThreadId = _BASE + 2
    Snippet = _BASE + 3
    Timestamp = _BASE + 4
    Unread = _BASE + 5
    Outgoing = _BASE + 6
    AppName = _BASE + 7
    Title = _BASE + 8
