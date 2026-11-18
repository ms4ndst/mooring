"""jeepney D-Bus connection driven by the Qt event loop.

Why not QtDBus: PyQt6's QDBusArgument has no read API, so KDE Connect's struct
payloads (SMS) can't be decoded. jeepney decodes any signature into plain tuples.

jeepney's blocking connection does the auth handshake and Hello. After that we
read from its socket only when a QSocketNotifier says data is waiting, so nothing
ever blocks the GUI thread. Everything runs on the GUI thread; no locks needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from jeepney import (
    DBusAddress,
    HeaderFields,
    MatchRule,
    Message,
    MessageType,
    message_bus,
    new_method_call,
)
from jeepney.io.blocking import DBusConnection, open_dbus_connection
from PyQt6.QtCore import QObject, QSocketNotifier, QTimer, pyqtSignal

log = logging.getLogger(__name__)

ReplyCallback = Callable[[tuple[Any, ...]], None]
PropsCallback = Callable[[dict[str, Any]], None]
#: (error name, error text). Text comes from the daemon; don't log it verbatim.
ErrorCallback = Callable[[str, str], None]
SignalHandler = Callable[[Message], None]

PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


class BusClient(Protocol):
    """What the backends need from a bus connection (lets tests swap in a fake)."""

    def call(
        self,
        addr: DBusAddress,
        method: str,
        signature: str = "",
        body: tuple[Any, ...] = (),
        on_reply: ReplyCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None: ...

    def get_all(
        self,
        addr: DBusAddress,
        on_reply: PropsCallback,
        on_error: ErrorCallback | None = None,
    ) -> None: ...

    def subscribe(self, rule: MatchRule, handler: SignalHandler) -> None: ...


def _log_error(name: str, _text: str) -> None:
    log.warning("D-Bus call failed: %s", name)


class DBusClient(QObject):
    """Async D-Bus client: replies and signals arrive as callbacks on the GUI thread."""

    #: The bus connection dropped (e.g. the session bus went away).
    disconnected = pyqtSignal()

    def __init__(self, conn: DBusConnection, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._pending: dict[int, tuple[ReplyCallback | None, ErrorCallback]] = {}
        self._subs: list[tuple[MatchRule, SignalHandler]] = []
        self._notifier = QSocketNotifier(conn.sock.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)
        QTimer.singleShot(0, self._drain)  # anything buffered during the handshake

    @classmethod
    def session(cls, parent: QObject | None = None) -> DBusClient:
        return cls(open_dbus_connection(bus="SESSION"), parent)

    # --- sending -------------------------------------------------------------

    def send(
        self,
        msg: Message,
        on_reply: ReplyCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        serial = next(self._conn.outgoing_serial)
        self._pending[serial] = (on_reply, on_error or _log_error)
        try:
            self._conn.send(msg, serial=serial)
        except OSError:
            self._pending.pop(serial, None)
            self._lost()

    def call(
        self,
        addr: DBusAddress,
        method: str,
        signature: str = "",
        body: tuple[Any, ...] = (),
        on_reply: ReplyCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.send(new_method_call(addr, method, signature, body), on_reply, on_error)

    def get_all(
        self,
        addr: DBusAddress,
        on_reply: PropsCallback,
        on_error: ErrorCallback | None = None,
    ) -> None:
        """Properties.GetAll, unwrapped to {name: value}."""
        props_addr = DBusAddress(addr.object_path, addr.bus_name, PROPERTIES_IFACE)

        def unwrap(body: tuple[Any, ...]) -> None:
            on_reply({k: v for k, (_sig, v) in body[0].items()})

        self.call(props_addr, "GetAll", "s", (addr.interface,), unwrap, on_error)

    def subscribe(self, rule: MatchRule, handler: SignalHandler) -> None:
        self._subs.append((rule, handler))
        self.send(message_bus.AddMatch(rule))

    def close(self) -> None:
        self._notifier.setEnabled(False)
        self._conn.close()

    # --- receiving -----------------------------------------------------------

    def _on_readable(self) -> None:
        try:
            data = self._conn.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            self._lost()
            return
        if not data:
            self._lost()
            return
        self._conn.parser.add_data(data)
        self._drain()

    def _drain(self) -> None:
        while (msg := self._conn.parser.get_next_message()) is not None:
            self._dispatch(msg)

    def _dispatch(self, msg: Message) -> None:
        kind = msg.header.message_type
        fields = msg.header.fields
        if kind in (MessageType.method_return, MessageType.error):
            entry = self._pending.pop(fields.get(HeaderFields.reply_serial, -1), None)
            if entry is None:
                return
            on_reply, on_error = entry
            if kind is MessageType.error:
                text = msg.body[0] if msg.body and isinstance(msg.body[0], str) else ""
                self._guard(on_error, fields.get(HeaderFields.error_name, "?"), text)
            elif on_reply is not None:
                self._guard(on_reply, msg.body)
        elif kind is MessageType.signal:
            for rule, handler in list(self._subs):
                if rule.matches(msg):
                    self._guard(handler, msg)

    @staticmethod
    def _guard(fn: Callable[..., None], *args: Any) -> None:
        # A failing callback must not kill the read loop. Log the exception *type*
        # only: messages may embed payload values (SMS text, numbers).
        try:
            fn(*args)
        except Exception as exc:
            log.error(
                "D-Bus callback %s raised %s",
                getattr(fn, "__qualname__", fn),
                type(exc).__name__,
                exc_info=log.isEnabledFor(logging.DEBUG),
            )

    def _lost(self) -> None:
        if not self._notifier.isEnabled():
            return
        log.warning("session bus connection lost")
        self._notifier.setEnabled(False)
        self._pending.clear()
        self.disconnected.emit()
