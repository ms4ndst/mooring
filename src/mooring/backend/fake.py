"""In-memory backend with synthetic data, for UI work without a phone.

All names, numbers and texts here are made up. Never paste real data in.
"""

from __future__ import annotations

import logging
import random
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer

from mooring.backend.base import (
    Attachment,
    Conversation,
    Device,
    Message,
    Notification,
    PhoneBackend,
)

log = logging.getLogger(__name__)

PHONE_ID = "fake-phone"
TABLET_ID = "fake-tablet"

_CONTACTS: list[tuple[str, tuple[str, ...]]] = [
    ("Alex Example", ("+46700000001",)),
    ("Sam Placeholder", ("+46700000002",)),
    ("Robin Testsson", ("+46700000003",)),
    ("+46700000004", ("+46700000004",)),
    ("Kim Fixture", ("+46700000005",)),
    ("Book club", ("+46700000006", "+46700000007")),
]

_LINES = [
    "Are we still on for tomorrow?",
    "Yes! See you at 6.",
    "Running about ten minutes late, sorry",
    "No worries",
    "Did you see the game last night?",
    "Can you pick up milk on the way home?",
    "Sure, anything else?",
    "Bread too please",
    "Happy birthday! 🎉",
    "Thanks so much!",
    "Sending the photos later tonight",
    "This is a longer message to check that the chat bubbles wrap properly when "
    "someone writes a whole paragraph instead of a quick reply.",
    "👍",
    "What time works for you?",
    "Anytime after lunch",
    "Got it, thanks",
]

_REPLIES = ["Sounds good!", "Haha, ok", "Will do 👍", "Let me check and get back to you"]


def _fake_png(width: int = 320, height: int = 200) -> bytes:
    """A synthetic 'sunset' gradient, so MMS rendering can be developed offline."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter

    img = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(img)
    grad = QLinearGradient(0, 0, 0, height)
    grad.setColorAt(0, QColor("#f5a97f"))
    grad.setColorAt(0.6, QColor("#c6a0f6"))
    grad.setColorAt(1, QColor("#24273a"))
    painter.fillRect(0, 0, width, height, grad)
    painter.setBrush(QColor("#eed49f"))
    painter.drawEllipse(width // 2 - 30, int(height * 0.55) - 30, 60, 60)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data.data())


def _fake_jpeg(path: Path, seed: int, size: tuple[int, int] = (800, 600)) -> None:
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    palette = ["#f5a97f", "#c6a0f6", "#8aadf4", "#a6da95", "#eed49f", "#f5bde6", "#8bd5ca"]
    a, b = rng.sample(palette, 2)
    img = Image.new("RGB", size, a)
    draw = ImageDraw.Draw(img)
    w, h = size
    draw.rectangle((0, int(h * rng.uniform(0.4, 0.7)), w, h), fill=b)
    r = rng.randint(40, 120)
    cx, cy = rng.randint(r, w - r), rng.randint(r, h // 2)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#f4dbd6")
    img.save(path, "JPEG", quality=80)


def _fake_storage(count: int, parent: str | None = None) -> str:
    """A throwaway 'phone storage' tree with synthetic pictures, like sshfs would show."""
    import os
    import tempfile
    import time

    base = Path(tempfile.mkdtemp(prefix="mooring-fake-phone-", dir=parent))
    root = base / "storage" / "emulated" / "0"
    camera, shots = root / "DCIM" / "Camera", root / "Pictures" / "Screenshots"
    hidden = root / "Pictures" / ".thumbnails"
    for d in (camera, shots, hidden, root / "Download"):
        d.mkdir(parents=True)
    now = time.time()
    for i in range(count):
        folder = shots if i % 5 == 4 else camera
        prefix = "Screenshot_" if folder is shots else "IMG_"
        path = folder / f"{prefix}{20260101 + i}.jpg"
        _fake_jpeg(path, seed=i)
        t = now - i * 3600
        os.utime(path, (t, t))
    (hidden / "should_be_skipped.jpg").write_bytes(b"not an image")
    return str(root)


class FakeBackend(PhoneBackend):
    def __init__(
        self,
        *,
        auto_reply_ms: int | None = 1500,
        messages_per_thread: int = 120,
        photo_count: int = 24,
        storage_parent: str | None = None,
        seed: int = 7,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._auto_reply_ms = auto_reply_ms
        self._rng = random.Random(seed)
        self._next_id = 1
        self._devices: dict[str, Device] = {
            PHONE_ID: Device(
                PHONE_ID, "Pixel (fake)", "phone", True, True, 82, False, ("Wi-Fi",), True
            ),
            TABLET_ID: Device(TABLET_ID, "Tablet (fake)", "tablet", False, True, None, False),
        }
        # device id -> thread id -> messages, oldest first
        self._threads: dict[str, dict[int, list[Message]]] = {PHONE_ID: {}, TABLET_ID: {}}
        self._names: dict[int, str] = {}
        self._unread: set[int] = set()
        self._notifications: dict[str, list[Notification]] = {PHONE_ID: [], TABLET_ID: []}
        self._generate(messages_per_thread)
        self._storage: dict[str, str] = {}
        self.photo_count = photo_count
        self._storage_parent = storage_parent
        self.mount_delay_ms = 0

    # --- data generation -----------------------------------------------------

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _generate(self, per_thread: int) -> None:
        now = datetime.now().replace(second=0, microsecond=0)
        for i, (name, addresses) in enumerate(_CONTACTS):
            thread_id = 1000 + i
            self._names[thread_id] = name
            ts = now - timedelta(hours=i * 7 + 1)
            msgs: list[Message] = []
            for _ in range(per_thread):
                msgs.append(
                    Message(
                        id=self._new_id(),
                        thread_id=thread_id,
                        addresses=addresses,
                        body=self._rng.choice(_LINES),
                        timestamp=ts,
                        outgoing=self._rng.random() < 0.45,
                    )
                )
                ts -= timedelta(minutes=self._rng.randint(3, 600))
            msgs.reverse()
            self._threads[PHONE_ID][thread_id] = msgs
        self._unread = {1000, 1002}

        # A picture message (MMS) a few messages from the end of Sam's thread
        sam = self._threads[PHONE_ID][1001]
        pic = Message(
            id=self._new_id(),
            thread_id=1001,
            addresses=sam[-1].addresses,
            body="Look at this sunset!",
            timestamp=sam[-3].timestamp + timedelta(seconds=30),
            outgoing=False,
            attachments=(Attachment(1, "image/png", "fake-sunset", _fake_png()),),
        )
        sam.insert(len(sam) - 2, pic)

        self._notifications[PHONE_ID] = [
            Notification("n1", "Calendar", "Standup in 10 minutes", "Room 3", now),
            Notification(
                "n2",
                "Chat",
                "Sam Placeholder",
                "Lunch tomorrow?",
                now - timedelta(minutes=12),
                repliable=True,
            ),
            Notification(
                "n3",
                "Weather",
                "Rain expected",
                "Bring an umbrella after 15:00",
                now - timedelta(hours=2),
            ),
            Notification(
                "n4",
                "Bank (fake)",
                "Card payment",
                "SEK 129.00 at Example Café",
                now - timedelta(hours=5),
            ),
        ]

    # --- lifecycle / devices -------------------------------------------------

    def start(self) -> None:
        self.devicesChanged.emit()

    def devices(self) -> list[Device]:
        return list(self._devices.values())

    def request_pairing(self, device_id: str) -> None:
        """Simulates the phone accepting at once."""
        log.info("fake: pairing requested for %s", device_id)
        self._set_trusted(device_id, True)

    def unpair(self, device_id: str) -> None:
        log.info("fake: unpair requested for %s", device_id)
        self._set_trusted(device_id, False)

    def _set_trusted(self, device_id: str, trusted: bool) -> None:
        if device_id in self._devices:
            self._devices[device_id] = replace(self._devices[device_id], trusted=trusted)
            self.deviceChanged.emit(device_id)
            self.devicesChanged.emit()

    def ring(self, device_id: str) -> None:
        log.info("fake: ring requested for %s", device_id)

    def set_reachable(self, device_id: str, reachable: bool) -> None:
        """Test hook: simulate the phone dropping off / coming back."""
        self._devices[device_id] = replace(self._devices[device_id], reachable=reachable)
        self.deviceChanged.emit(device_id)

    # --- messages ------------------------------------------------------------

    def conversations(self, device_id: str) -> list[Conversation]:
        return [
            Conversation(
                thread_id=tid,
                addresses=msgs[-1].addresses,
                display_name=self._names.get(tid, ", ".join(msgs[-1].addresses)),
                last_message=msgs[-1],
                unread=tid in self._unread,
            )
            for tid, msgs in self._threads.get(device_id, {}).items()
            if msgs
        ]

    def request_conversation(self, device_id: str, thread_id: int, start: int, end: int) -> None:
        msgs = self._threads.get(device_id, {}).get(thread_id, [])
        total = len(msgs)
        chunk = msgs[max(0, total - end) : max(0, total - start)]
        mark_read = start == 0 and thread_id in self._unread

        def deliver() -> None:
            self.messagesLoaded.emit(device_id, thread_id, start, chunk)
            if mark_read:
                self._unread.discard(thread_id)
                self.conversationsChanged.emit(device_id)

        QTimer.singleShot(0, deliver)

    def send_message(
        self,
        device_id: str,
        addresses: tuple[str, ...],
        text: str,
        thread_id: int | None = None,
    ) -> None:
        threads = self._threads.setdefault(device_id, {})
        if thread_id is None:
            thread_id = next(
                (t for t, m in threads.items() if m and m[-1].addresses == addresses),
                max(threads, default=1999) + 1,
            )
        msg = Message(self._new_id(), thread_id, addresses, text, datetime.now(), outgoing=True)
        threads.setdefault(thread_id, []).append(msg)
        log.info("fake: sent message to thread %s", thread_id)
        self.messageAdded.emit(device_id, msg)
        self.conversationsChanged.emit(device_id)
        if self._auto_reply_ms is not None:
            tid = thread_id
            QTimer.singleShot(self._auto_reply_ms, lambda: self._receive(device_id, tid))

    def _receive(self, device_id: str, thread_id: int) -> None:
        msgs = self._threads[device_id][thread_id]
        msg = Message(
            self._new_id(),
            thread_id,
            msgs[-1].addresses,
            self._rng.choice(_REPLIES),
            datetime.now(),
            outgoing=False,
            read=False,
        )
        msgs.append(msg)
        self._unread.add(thread_id)
        self.messageAdded.emit(device_id, msg)
        self.conversationsChanged.emit(device_id)

    # --- notifications -------------------------------------------------------

    def notifications(self, device_id: str) -> list[Notification]:
        return list(self._notifications.get(device_id, []))

    def dismiss_notification(self, device_id: str, notification_id: str) -> None:
        items = self._notifications.get(device_id, [])
        self._notifications[device_id] = [n for n in items if n.id != notification_id]
        self.notificationRemoved.emit(device_id, notification_id)

    def post_notification(self, device_id: str, notification: Notification) -> None:
        """Test hook: simulate the phone posting a notification."""
        self._notifications.setdefault(device_id, []).insert(0, notification)
        self.notificationPosted.emit(device_id, notification)

    # --- sharing -------------------------------------------------------------

    def send_clipboard(self, device_id: str) -> None:
        log.info("fake: clipboard sent to %s", device_id)

    def share_url(self, device_id: str, url: str) -> None:
        log.info("fake: shared one url to %s", device_id)

    # --- storage -------------------------------------------------------------

    def storage_root(self, device_id: str) -> str | None:
        return self._storage.get(device_id)

    def mount_storage(self, device_id: str) -> None:
        if device_id not in self._storage:
            self._storage[device_id] = _fake_storage(self.photo_count, self._storage_parent)
        root = self._storage[device_id]
        QTimer.singleShot(self.mount_delay_ms, lambda: self.storageReady.emit(device_id, root))

    def add_photo(self, device_id: str, name: str = "IMG_new.jpg") -> str:
        """Test hook: 'take a photo' on the fake phone."""
        root = Path(self._storage[device_id])
        path = root / "DCIM" / "Camera" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _fake_jpeg(path, seed=len(name))
        return str(path)

    # --- clipboard -----------------------------------------------------------

    def set_clipboard_sync(self, device_id: str, enabled: bool) -> None:
        if device_id in self._devices:
            self._devices[device_id] = replace(self._devices[device_id], clipboard_sync=enabled)
            self.deviceChanged.emit(device_id)

    def request_attachment(self, device_id: str, attachment: Attachment) -> None:
        import tempfile
        from pathlib import Path

        path = Path(tempfile.gettempdir()) / f"mooring-fake-{attachment.unique_id}.png"
        path.write_bytes(attachment.thumbnail)
        QTimer.singleShot(0, lambda: self.attachmentReady.emit(device_id, str(path)))
