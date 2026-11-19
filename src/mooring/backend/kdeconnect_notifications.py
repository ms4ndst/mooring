"""Phone notifications via kdeconnectd's notifications plugin. Owned by KdeConnectBackend.

Interface (verified live, kdeconnectd 26.08):
- ``.../devices/<id>/notifications`` · org.kde.kdeconnect.device.notifications
  activeNotifications() -> as (public ids); signals notificationPosted(s),
  notificationUpdated(s), notificationRemoved(s), allNotificationsRemoved()
- ``.../notifications/<publicId>`` · ...notifications.notification
  properties appName, title, text, ticker, dismissable, replyId, iconPath, hasIcon, silent;
  methods dismiss(), sendReply(s); signal ready() (icon arrived)

There's no timestamp on the D-Bus side; we use the time we first saw a notification.
kdeconnectd already shows phone notifications as desktop popups, so we only list them.

Privacy: notification text is shown in the UI only. Log counts and app-independent
events, never titles, texts or app names.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from jeepney import DBusAddress
from jeepney import Message as DBusMessage
from PyQt6.QtCore import QObject

from mooring.backend import dbus_paths as P
from mooring.backend.base import Notification

if TYPE_CHECKING:
    from mooring.backend.kdeconnect import KdeConnectBackend

log = logging.getLogger(__name__)


class NotificationChannel(QObject):
    def __init__(self, backend: KdeConnectBackend) -> None:
        super().__init__(backend)
        self._b = backend
        #: device -> public id -> notification
        self._items: dict[str, dict[str, Notification]] = {}

    # --- addresses -----------------------------------------------------------

    @staticmethod
    def _list_addr(device_id: str) -> DBusAddress:
        return DBusAddress(
            P.plugin_path(device_id, P.NOTIFICATIONS_SUFFIX), P.SERVICE, P.NOTIFICATIONS_IFACE
        )

    @staticmethod
    def _item_addr(device_id: str, public_id: str) -> DBusAddress:
        path = f"{P.plugin_path(device_id, P.NOTIFICATIONS_SUFFIX)}/{public_id}"
        return DBusAddress(path, P.SERVICE, P.NOTIFICATION_IFACE)

    @staticmethod
    def public_id_from_path(path: str) -> str | None:
        """'.../devices/<dev>/notifications/<publicId>' -> '<publicId>'."""
        head, _, tail = path.rpartition("/")
        return tail if head.endswith("/" + P.NOTIFICATIONS_SUFFIX) and tail else None

    # --- lifecycle -----------------------------------------------------------

    def device_ready(self, device_id: str) -> None:
        bus = self._b._bus
        if bus is None:
            return

        def got(body: tuple[Any, ...]) -> None:
            ids = [str(i) for i in body[0]]
            log.info("notifications: %d active on %s", len(ids), device_id[:6])
            for public_id in ids:
                self._fetch(device_id, public_id)

        bus.call(
            self._list_addr(device_id),
            "activeNotifications",
            on_reply=got,
            on_error=lambda name, _t: log.info("notifications unavailable: %s", name),
        )

    def forget_device(self, device_id: str) -> None:
        for public_id in list(self._items.pop(device_id, {})):
            self._b.notificationRemoved.emit(device_id, public_id)

    def notifications(self, device_id: str) -> list[Notification]:
        return list(self._items.get(device_id, {}).values())

    # --- loading -------------------------------------------------------------

    def _fetch(self, device_id: str, public_id: str) -> None:
        bus = self._b._bus
        if bus is None:
            return

        def got(props: dict[str, Any]) -> None:
            old = self._items.get(device_id, {}).get(public_id)
            n = Notification(
                id=public_id,
                app_name=str(props.get("appName", "")),
                title=str(props.get("title", "")),
                text=str(props.get("text", "")) or str(props.get("ticker", "")),
                timestamp=old.timestamp if old else datetime.now(),
                dismissable=bool(props.get("dismissable", False)),
                repliable=bool(props.get("replyId", "")),
                icon_path=str(props.get("iconPath", "")) if props.get("hasIcon") else "",
            )
            if n == old:
                return
            self._items.setdefault(device_id, {})[public_id] = n
            self._b.notificationPosted.emit(device_id, n)

        bus.get_all(
            self._item_addr(device_id, public_id),
            on_reply=got,
            on_error=lambda _n, _t: None,  # removed again before we asked
        )

    # --- signals -------------------------------------------------------------

    def on_list_signal(self, msg: DBusMessage, member: str, device_id: str) -> None:
        if member in ("notificationPosted", "notificationUpdated"):
            self._fetch(device_id, str(msg.body[0]))
        elif member == "notificationRemoved":
            public_id = str(msg.body[0])
            if self._items.get(device_id, {}).pop(public_id, None) is not None:
                self._b.notificationRemoved.emit(device_id, public_id)
        elif member == "allNotificationsRemoved":
            self.forget_device(device_id)

    def on_item_signal(self, msg: DBusMessage, member: str, device_id: str, path: str) -> None:
        if member == "ready" and (public_id := self.public_id_from_path(path)):
            self._fetch(device_id, public_id)  # icon (or late fields) now available

    # --- actions -------------------------------------------------------------

    def dismiss(self, device_id: str, public_id: str) -> None:
        bus = self._b._bus
        if bus is None:
            return
        bus.call(
            self._item_addr(device_id, public_id),
            "dismiss",
            on_error=lambda _n, _t: self._b.error.emit("Couldn't dismiss the notification"),
        )

    def reply(self, device_id: str, public_id: str, text: str) -> None:
        bus = self._b._bus
        if bus is None:
            return
        bus.call(
            self._item_addr(device_id, public_id),
            "sendReply",
            "s",
            (text,),
            on_error=lambda _n, _t: self._b.error.emit("Couldn't send the reply"),
        )
        log.info("notifications: replied to one notification")
