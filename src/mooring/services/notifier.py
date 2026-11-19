"""Desktop notifications for incoming SMS via org.freedesktop.Notifications.

- "Open" (default action) and "Reply" bring Mooring up on that thread.
- If the daemon advertises ``inline-reply`` (KDE Plasma, swaync), the reply typed
  in the notification is sent straight away. dunst/mako don't support it.
- Actions are only accepted from the process that owns the notification service,
  and only for notifications we created: a forged ``NotificationReplied`` would
  otherwise make Mooring send an SMS.

The notification shows the sender and the message (unless the user turned that off).
Nothing is logged except IDs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from jeepney import DBusAddress, HeaderFields, MatchRule
from jeepney import Message as DBusMessage
from PyQt6.QtCore import QObject

from mooring.backend.base import Message, PhoneBackend
from mooring.backend.qjeepney import BusClient, DBusClient
from mooring.resources.icons import APP_ID
from mooring.services.settings import SettingsStore

log = logging.getLogger(__name__)

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
NOTIFICATIONS = DBusAddress(PATH, SERVICE, SERVICE)
BUS_DAEMON = DBusAddress("/org/freedesktop/DBus", "org.freedesktop.DBus", "org.freedesktop.DBus")

#: Don't notify for messages older than this (history arriving during a sync).
MAX_AGE = timedelta(minutes=2)
BODY_LIMIT = 300


class Notifier(QObject):
    def __init__(
        self,
        backend: PhoneBackend,
        settings: SettingsStore,
        *,
        open_thread: Callable[[str, int], None],
        is_viewing: Callable[[str, int], bool],
        client_factory: Callable[[], BusClient] = DBusClient.session,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend, self._settings = backend, settings
        self._open_thread, self._is_viewing = open_thread, is_viewing
        self._factory = client_factory
        self._bus: BusClient | None = None
        self._owner: str | None = None
        self._caps: set[str] = set()
        #: notification id -> (device id, thread id, addresses)
        self._ours: dict[int, tuple[str, int, tuple[str, ...]]] = {}
        #: thread -> notification id, so a new message replaces the previous popup
        self._by_thread: dict[tuple[str, int], int] = {}

    def start(self) -> None:
        try:
            self._bus = self._factory()
        except OSError:
            log.warning("notifications disabled: no session bus")
            return
        bus = self._bus
        for member in ("ActionInvoked", "NotificationReplied", "NotificationClosed"):
            bus.subscribe(
                MatchRule(type="signal", interface=SERVICE, member=member, path=PATH),
                self._on_signal,
            )
        bus.call(
            BUS_DAEMON,
            "GetNameOwner",
            "s",
            (SERVICE,),
            on_reply=lambda b: setattr(self, "_owner", str(b[0])),
            on_error=lambda n, _t: log.info("no notification daemon running (%s)", n),
        )
        bus.call(
            NOTIFICATIONS,
            "GetCapabilities",
            on_reply=lambda b: setattr(self, "_caps", set(b[0])),
            on_error=lambda _n, _t: None,
        )
        self._backend.messageAdded.connect(self._on_message)

    @property
    def inline_reply(self) -> bool:
        return "inline-reply" in self._caps

    # --- posting -------------------------------------------------------------

    def _on_message(self, device_id: str, msg: Message) -> None:
        if msg.outgoing or datetime.now() - msg.timestamp > MAX_AGE:
            return
        if self._is_viewing(device_id, msg.thread_id) or self._bus is None:
            return
        conv = next(
            (c for c in self._backend.conversations(device_id) if c.thread_id == msg.thread_id),
            None,
        )
        title = conv.display_name if conv else ", ".join(msg.addresses)
        if not self._settings.notify_show_text:
            body = "New message"
        elif msg.body:
            body = msg.body if len(msg.body) <= BODY_LIMIT else msg.body[: BODY_LIMIT - 1] + "…"
        else:
            body = "Picture message" if msg.attachments else "New message"
        if "body-markup" in self._caps:
            body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        actions = ["default", "Open", "reply", "Reply"]
        if self.inline_reply:
            actions += ["inline-reply", "Reply"]
        hints: dict[str, Any] = {
            "category": ("s", "im.received"),
            "desktop-entry": ("s", APP_ID),
            "x-dunst-stack-tag": ("s", f"mooring-{device_id[:6]}-{msg.thread_id}"),
        }
        key = (device_id, msg.thread_id)
        replaces = self._by_thread.get(key, 0)

        def posted(reply: tuple[Any, ...]) -> None:
            nid = int(reply[0])
            self._ours[nid] = (device_id, msg.thread_id, msg.addresses)
            self._by_thread[key] = nid

        self._bus.call(
            NOTIFICATIONS,
            "Notify",
            "susssasa{sv}i",
            ("Mooring", replaces, APP_ID, title, body, actions, hints, -1),
            on_reply=posted,
        )

    # --- actions -------------------------------------------------------------

    def _on_signal(self, msg: DBusMessage) -> None:
        sender = str(msg.header.fields.get(HeaderFields.sender, ""))
        if self._owner is None or sender != self._owner:
            log.debug("ignored notification signal from a non-daemon sender")
            return
        member = str(msg.header.fields.get(HeaderFields.member, ""))
        nid = int(msg.body[0])
        target = self._ours.get(nid)
        if target is None:
            return
        device_id, thread_id, addresses = target
        if member == "ActionInvoked" and msg.body[1] in ("default", "reply"):
            self._open_thread(device_id, thread_id)
        elif member == "NotificationReplied":
            text = str(msg.body[1]).strip()
            if text:
                self._backend.send_message(device_id, addresses, text, thread_id)
        elif member == "NotificationClosed":
            self._ours.pop(nid, None)
            if self._by_thread.get((device_id, thread_id)) == nid:
                del self._by_thread[(device_id, thread_id)]
