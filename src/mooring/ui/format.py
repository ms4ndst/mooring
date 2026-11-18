from __future__ import annotations

from datetime import datetime, timedelta


def short_time(ts: datetime, now: datetime | None = None) -> str:
    """'14:05' today, 'Tue' this week, '2026-03-01' otherwise."""
    now = now or datetime.now()
    if ts.date() == now.date():
        return ts.strftime("%H:%M")
    if now - ts < timedelta(days=7):
        return ts.strftime("%a")
    return ts.strftime("%Y-%m-%d")


def initials(name: str) -> str:
    stripped = name.strip()
    if not stripped or stripped[0] in "+0123456789":
        return "#"
    parts = stripped.split()
    return "".join(p[0] for p in parts[:2]).upper()
