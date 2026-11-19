"""SMS over kdeconnectd's conversations interface. Owned by KdeConnectBackend.

Protocol facts this relies on (docs/dbus-introspection.txt, KDE source):
- ``activeConversations()`` -> newest message of every cached thread.
- ``requestConversation(thread, start, end)`` replies with one ``conversationUpdated``
  per message, newest first, indices counted from the newest. A request served from
  the daemon's cache has NO end marker on D-Bus; ``conversationLoaded(thread, total)``
  only fires when the daemon had to fetch from the phone. So a page is complete when
  we have ``end - start`` messages, or nothing new arrived for QUIET_MS.
- ``conversationUpdated`` / ``conversationCreated`` also announce brand-new messages.
- The daemon doesn't bounds-check ``start`` beyond its cache; we never ask for that.

Privacy: log thread IDs and counts only, never bodies or addresses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jeepney import DBusAddress
from jeepney import Message as DBusMessage
from PyQt6.QtCore import QObject, QTimer

from mooring.backend import dbus_paths as P
from mooring.backend.base import Attachment, Conversation, Message
from mooring.backend.sms_decode import decode_message, encode_addresses
from mooring.services.contacts import ContactBook

if TYPE_CHECKING:
    from mooring.backend.kdeconnect import KdeConnectBackend

log = logging.getLogger(__name__)

#: Wait this long for the first message of a page (the daemon may be asking the phone).
FIRST_WAIT_MS = 20_000
#: Once messages flow, a page is done after this much silence.
QUIET_MS = 400
#: Coalesce bursts of conversation signals (e.g. a full sync) into one UI refresh.
REFRESH_MS = 100
#: If a sync request gets no conversation data at all within this time, tell the user.
SYNC_WAIT_MS = 30_000

NO_ANSWER_BLUETOOTH = (
    "The phone didn't send any messages. KDE Connect's Bluetooth link (beta) can stall "
    "and swallow SMS traffic: turn Bluetooth off on the phone to use Wi-Fi only."
)
NO_ANSWER = (
    "The phone didn't send any messages. Check that KDE Connect has SMS permission "
    "and unrestricted battery use on the phone."
)


@dataclass
class _Page:
    start: int
    want: int
    timer: QTimer
    messages: dict[int, Message] = field(default_factory=dict)
    loaded_signal: bool = False


class SmsChannel(QObject):
    def __init__(self, backend: KdeConnectBackend) -> None:
        super().__init__(backend)
        self._b = backend
        #: device -> thread -> newest known message
        self._latest: dict[str, dict[int, Message]] = {}
        #: (device, thread) -> id of the newest message the user has seen in Mooring
        self._read_upto: dict[tuple[str, int], int] = {}
        #: (device, thread) -> total messages the daemon reported caching
        self._totals: dict[tuple[str, int], int] = {}
        self._pages: dict[tuple[str, int], _Page] = {}
        self._contacts: dict[str, ContactBook] = {}
        self._synced: set[str] = set()
        self._dirty: set[str] = set()
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(REFRESH_MS)
        self._refresh.timeout.connect(self._emit_dirty)
        #: device -> timer waiting for the first conversation data after a sync request
        self._sync_watch: dict[str, QTimer] = {}

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _conv_addr(device_id: str) -> DBusAddress:
        return DBusAddress(P.device_path(device_id), P.SERVICE, P.CONVERSATIONS_IFACE)

    def _call(
        self,
        device_id: str,
        method: str,
        sig: str = "",
        body: tuple[Any, ...] = (),
        on_reply: Any = None,
        failure: str = "",
    ) -> None:
        bus = self._b._bus
        if bus is None:
            self._b.error.emit("Not connected to KDE Connect")
            return

        def on_error(name: str, _text: str) -> None:
            log.warning("%s on %s failed: %s", method, device_id[:6], name)
            if failure:
                self._b.error.emit(failure)

        bus.call(self._conv_addr(device_id), method, sig, body, on_reply, on_error)

    def _mark_dirty(self, device_id: str) -> None:
        self._dirty.add(device_id)
        self._refresh.start()

    def _emit_dirty(self) -> None:
        dirty, self._dirty = self._dirty, set()
        for device_id in dirty:
            self._b.conversationsChanged.emit(device_id)

    def _book(self, device_id: str) -> ContactBook:
        if device_id not in self._contacts:
            self._contacts[device_id] = ContactBook.for_device(device_id)
        return self._contacts[device_id]

    def _store_latest(self, device_id: str, msg: Message) -> bool:
        """Remember msg if it's the newest of its thread. True if it was newer."""
        threads = self._latest.setdefault(device_id, {})
        current = threads.get(msg.thread_id)
        if current is not None and (msg.timestamp, msg.id) <= (current.timestamp, current.id):
            return False
        threads[msg.thread_id] = msg
        return True

    # --- device lifecycle ----------------------------------------------------

    def device_ready(self, device_id: str) -> None:
        """Called when a paired device becomes reachable: load and (once) sync threads."""

        def got(body: tuple[Any, ...]) -> None:
            decoded = [m for m in map(decode_message, body[0]) if m is not None]
            for m in decoded:
                self._store_latest(device_id, m)
            log.info("sms: %d conversation(s) cached for %s", len(decoded), device_id[:6])
            self._mark_dirty(device_id)

        self._call(device_id, "activeConversations", on_reply=got)
        if device_id not in self._synced:
            self._synced.add(device_id)
            self._call(device_id, "requestAllConversationThreads")
            self._watch_sync(device_id)

    def _watch_sync(self, device_id: str) -> None:
        self._stop_sync_watch(device_id)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._sync_unanswered(device_id))
        self._sync_watch[device_id] = timer
        timer.start(SYNC_WAIT_MS)

    def _stop_sync_watch(self, device_id: str) -> None:
        if timer := self._sync_watch.pop(device_id, None):
            timer.stop()
            timer.deleteLater()

    def _sync_unanswered(self, device_id: str) -> None:
        self._stop_sync_watch(device_id)
        if self._latest.get(device_id):
            return  # the daemon's cache had data; the phone just had nothing new
        device = self._b.device(device_id)
        if device is None or not device.reachable:
            return  # went offline meanwhile; the header already says so
        log.warning("sms: no answer to sync from %s (links: %s)", device_id[:6], device.links)
        # Seen live: a dead Bluetooth link swallowed SMS traffic even with Wi-Fi up too.
        on_bluetooth = "Bluetooth" in device.links
        self._b.error.emit(NO_ANSWER_BLUETOOTH if on_bluetooth else NO_ANSWER)
        self._synced.discard(device_id)  # try again on the next reconnect

    def forget_device(self, device_id: str) -> None:
        self._stop_sync_watch(device_id)
        self._latest.pop(device_id, None)
        self._synced.discard(device_id)
        self._mark_dirty(device_id)

    # --- reading -------------------------------------------------------------

    def conversations(self, device_id: str) -> list[Conversation]:
        book = self._book(device_id)
        result = []
        for thread_id, m in self._latest.get(device_id, {}).items():
            seen = self._read_upto.get((device_id, thread_id))
            result.append(
                Conversation(
                    thread_id=thread_id,
                    addresses=m.addresses,
                    display_name=book.display_name(m.addresses),
                    last_message=m,
                    unread=not m.outgoing and not m.read and seen != m.id,
                )
            )
        return result

    def request_conversation(self, device_id: str, thread_id: int, start: int, end: int) -> None:
        key = (device_id, thread_id)
        total = self._totals.get(key)
        if total is not None and start > total:  # the daemon doesn't bounds-check this
            self._b.messagesLoaded.emit(device_id, thread_id, start, [])
            return
        if old := self._pages.pop(key, None):
            old.timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._flush(key))
        self._pages[key] = _Page(start, end - start, timer)
        timer.start(FIRST_WAIT_MS)
        if start == 0:
            latest = self._latest.get(device_id, {}).get(thread_id)
            if latest is not None:
                self._read_upto[key] = latest.id
                self._mark_dirty(device_id)
        self._call(
            device_id,
            "requestConversation",
            "xii",
            (thread_id, start, end),
            failure="Couldn't load the conversation",
        )

    def _flush(self, key: tuple[str, int]) -> None:
        page = self._pages.pop(key, None)
        if page is None:
            return
        page.timer.stop()
        page.timer.deleteLater()
        messages = sorted(page.messages.values(), key=lambda m: (m.timestamp, m.id))
        if not messages and not page.loaded_signal and page.start == 0:
            self._b.error.emit("The phone didn't send the conversation")
        log.debug("sms: thread %d page @%d -> %d message(s)", key[1], page.start, len(messages))
        self._b.messagesLoaded.emit(key[0], key[1], page.start, messages)

    # --- signals -------------------------------------------------------------

    def on_signal(self, msg: DBusMessage, member: str, device_id: str) -> None:
        if member != "attachmentReceived":
            self._stop_sync_watch(device_id)  # the phone is talking to us
        if member in ("conversationCreated", "conversationUpdated"):
            m = decode_message(msg.body[0]) if msg.body else None
            if m is not None:
                self._on_message(device_id, m)
        elif member == "conversationLoaded":
            thread_id, total = int(msg.body[0]), int(msg.body[1])
            key = (device_id, thread_id)
            self._totals[key] = total
            if page := self._pages.get(key):
                page.loaded_signal = True
                page.timer.start(QUIET_MS)  # worker may still be emitting; settle briefly
        elif member == "conversationRemoved":
            self._latest.get(device_id, {}).pop(int(msg.body[0]), None)
            self._mark_dirty(device_id)
        elif member == "attachmentReceived":
            self._b.attachmentReady.emit(device_id, str(msg.body[0]))

    def _on_message(self, device_id: str, m: Message) -> None:
        key = (device_id, m.thread_id)
        if page := self._pages.get(key):
            page.messages[m.id] = m
            if len(page.messages) >= page.want:
                self._flush(key)
            else:
                page.timer.start(QUIET_MS)
        if self._store_latest(device_id, m):
            # Newer than anything we had for the thread: a new or just-sent message.
            self._b.messageAdded.emit(device_id, m)
            self._mark_dirty(device_id)

    # --- writing -------------------------------------------------------------

    def send(
        self, device_id: str, addresses: tuple[str, ...], text: str, thread_id: int | None
    ) -> None:
        failure = "Couldn't send the message"
        if thread_id is not None and thread_id in self._latest.get(device_id, {}):
            self._call(
                device_id, "replyToConversation", "xsav", (thread_id, text, []), failure=failure
            )
        else:
            self._call(
                device_id,
                "sendWithoutConversation",
                "avsav",
                (encode_addresses(addresses), text, []),
                failure=failure,
            )
        log.info("sms: sent a message (thread %s)", thread_id if thread_id is not None else "new")

    def request_attachment(self, device_id: str, attachment: Attachment) -> None:
        self._call(
            device_id,
            "requestAttachmentFile",
            "xs",
            (attachment.part_id, attachment.unique_id),
            failure="Couldn't download the attachment",
        )
