"""In-memory stand-in for qjeepney.DBusClient: scripted replies, real jeepney signals.

Replies are delivered synchronously, which is harsher than real life (the backend
must not assume ordering beyond what a callback guarantees).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jeepney import DBusAddress, HeaderFields, MatchRule, Message, new_signal

from mooring.backend.qjeepney import ErrorCallback, PropsCallback, ReplyCallback, SignalHandler

UNKNOWN_OBJECT = "org.freedesktop.DBus.Error.UnknownObject"
SERVICE_UNKNOWN = "org.freedesktop.DBus.Error.ServiceUnknown"


class FakeBus:
    def __init__(self) -> None:
        #: (path, interface, method) -> fn(*body) -> reply body tuple
        self.methods: dict[tuple[str, str, str], Callable[..., tuple[Any, ...]]] = {}
        #: (path, interface) -> {property: value}
        self.props: dict[tuple[str, str], dict[str, Any]] = {}
        #: (path, interface, method, body, signature)
        self.calls: list[tuple[str, str, str, tuple[Any, ...], str]] = []
        self.subs: list[tuple[MatchRule, SignalHandler]] = []
        self.daemon_running = True
        #: well-known name -> unique name (what GetNameOwner answers)
        self.owners: dict[str, str] = {
            "org.kde.kdeconnect": ":1.42",  # default sender of emit()
            "org.freedesktop.Notifications": ":1.50",
        }

    # --- BusClient protocol --------------------------------------------------

    def call(
        self,
        addr: DBusAddress,
        method: str,
        signature: str = "",
        body: tuple[Any, ...] = (),
        on_reply: ReplyCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        key = (addr.object_path, addr.interface or "", method)
        self.calls.append((*key, body, signature))
        if method == "GetNameOwner":
            owner = self.owners.get(body[0])
            if body[0] == "org.kde.kdeconnect" and not self.daemon_running:
                owner = None
            if owner and on_reply:
                on_reply((owner,))
            elif not owner and on_error:
                on_error("org.freedesktop.DBus.Error.NameHasNoOwner", "")
            return
        fn = self.methods.get(key)
        if not self.daemon_running or fn is None:
            if on_error:
                on_error(SERVICE_UNKNOWN if not self.daemon_running else UNKNOWN_OBJECT, "")
            return
        reply = fn(*body)
        if on_reply:
            on_reply(reply)

    def get_all(
        self, addr: DBusAddress, on_reply: PropsCallback, on_error: ErrorCallback | None = None
    ) -> None:
        props = self.props.get((addr.object_path, addr.interface or ""))
        if not self.daemon_running or props is None:
            if on_error:
                on_error(UNKNOWN_OBJECT, "")
            return
        on_reply(dict(props))

    def subscribe(self, rule: MatchRule, handler: SignalHandler) -> None:
        self.subs.append((rule, handler))

    # --- test helpers --------------------------------------------------------

    def emit(
        self,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: tuple[Any, ...] = (),
        sender: str = ":1.42",
    ) -> None:
        msg: Message = new_signal(DBusAddress(path, interface=interface), member, signature, body)
        msg.header.fields[HeaderFields.sender] = sender
        for rule, handler in list(self.subs):
            if rule.matches(msg):
                handler(msg)

    def called(self, method: str) -> list[tuple[str, str, str, tuple[Any, ...], str]]:
        return [c for c in self.calls if c[2] == method]
