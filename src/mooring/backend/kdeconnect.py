"""PhoneBackend on top of kdeconnectd's D-Bus API (via jeepney, see qjeepney.py).

Devices, pairing, reachability, links, battery, ring, clipboard, share (Phase 2) and
SMS via SmsChannel (Phase 3, kdeconnect_sms.py). Phone notifications: not yet.

Privacy: log device IDs (shortened) and counts only. Device names are user-chosen
and may contain a person's name, so they're never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from jeepney import DBusAddress, HeaderFields, MatchRule, Message
from PyQt6.QtCore import QObject, QTimer

from mooring.backend import dbus_paths as P
from mooring.backend.base import Attachment, Conversation, Device, Notification, PhoneBackend
from mooring.backend.kdeconnect_sms import SmsChannel
from mooring.backend.qjeepney import BusClient, DBusClient

log = logging.getLogger(__name__)

DAEMON = DBusAddress(P.DAEMON_PATH, P.SERVICE, P.DAEMON_IFACE)


def short(device_id: str) -> str:
    return device_id[:6]


def link_names(providers: Any) -> tuple[str, ...]:
    """kdeconnectd provider names ('LAN', 'Bluetooth (beta)') -> UI names, Wi-Fi first."""
    names: list[str] = []
    for provider in providers or ():
        low = str(provider).lower()
        name = (
            "Wi-Fi"
            if low.startswith("lan")
            else "Bluetooth"
            if "bluetooth" in low
            else str(provider)
        )
        if name not in names:
            names.append(name)
    order = {"Wi-Fi": 0, "Bluetooth": 1}
    return tuple(sorted(names, key=lambda n: order.get(n, 2)))


def _member(msg: Message) -> str:
    return str(msg.header.fields.get(HeaderFields.member, ""))


def _path(msg: Message) -> str:
    return str(msg.header.fields.get(HeaderFields.path, ""))


def _sender(msg: Message) -> str:
    return str(msg.header.fields.get(HeaderFields.sender, ""))


BUS_DAEMON = DBusAddress("/org/freedesktop/DBus", "org.freedesktop.DBus", "org.freedesktop.DBus")


class KdeConnectBackend(PhoneBackend):
    def __init__(
        self,
        client_factory: Callable[[], BusClient] = DBusClient.session,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._factory = client_factory
        self._bus: BusClient | None = None
        self._devices: dict[str, Device] = {}
        #: Unique bus name (":1.N") currently owning org.kde.kdeconnect. Signals from
        #: anyone else are dropped: any session process can emit a look-alike signal.
        self._owner: str | None = None
        self._generation = 0  # discards replies from superseded reloads
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(150)  # coalesce bursts of daemon signals
        self._reload_timer.timeout.connect(self.reload_devices)
        self._sms = SmsChannel(self)

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        try:
            self._bus = self._factory()
        except OSError as exc:
            log.error("cannot connect to the session bus: %s", type(exc).__name__)
            self.error.emit("Can't connect to the D-Bus session bus")
            return
        if isinstance(self._bus, DBusClient):
            self._bus.disconnected.connect(self._on_bus_lost)

        bus = self._bus
        bus.subscribe(
            MatchRule(type="signal", interface=P.DAEMON_IFACE, path=P.DAEMON_PATH),
            self._from_daemon(self._on_daemon_signal),
        )
        bus.subscribe(
            MatchRule(type="signal", interface=P.DEVICE_IFACE, path_namespace=P.DEVICES_NAMESPACE),
            self._from_daemon(self._on_device_signal),
        )
        bus.subscribe(
            MatchRule(
                type="signal",
                interface=P.BATTERY_IFACE,
                member="refreshed",
                path_namespace=P.DEVICES_NAMESPACE,
            ),
            self._from_daemon(self._on_battery_refreshed),
        )
        bus.subscribe(
            MatchRule(
                type="signal",
                interface=P.CONVERSATIONS_IFACE,
                path_namespace=P.DEVICES_NAMESPACE,
            ),
            self._from_daemon(self._on_conversation_signal),
        )
        owner_rule = MatchRule(
            type="signal",
            sender="org.freedesktop.DBus",
            interface="org.freedesktop.DBus",
            member="NameOwnerChanged",
            path="/org/freedesktop/DBus",
        )
        owner_rule.add_arg_condition(0, P.SERVICE)
        # The rule's sender check is enforced client-side too, against the header the
        # bus daemon stamps, so a process can't fake NameOwnerChanged either.
        bus.subscribe(owner_rule, self._on_owner_changed)
        bus.call(
            BUS_DAEMON,
            "GetNameOwner",
            "s",
            (P.SERVICE,),
            on_reply=self._set_owner,
            on_error=lambda _n, _t: None,  # not running yet; NameOwnerChanged will tell us
        )
        self.reload_devices()  # also D-Bus-activates kdeconnectd if it isn't running

    def _set_owner(self, body: tuple[Any, ...]) -> None:
        self._owner = str(body[0]) or None

    def _from_daemon(self, handler: Callable[[Message], None]) -> Callable[[Message], None]:
        def checked(msg: Message) -> None:
            if self._owner is not None and _sender(msg) == self._owner:
                handler(msg)
            else:
                log.debug("dropped %s from non-daemon sender", _member(msg))

        return checked

    # --- device list ---------------------------------------------------------

    def devices(self) -> list[Device]:
        return list(self._devices.values())

    def reload_devices(self) -> None:
        if self._bus is None:
            return
        self._generation += 1
        gen = self._generation
        self._bus.call(
            DAEMON,
            "devices",
            "bb",
            (False, False),  # all known devices; Device.trusted says which are paired
            on_reply=lambda body: self._got_device_ids(gen, list(body[0])),
            on_error=self._on_daemon_unavailable,
        )

    def _got_device_ids(self, gen: int, ids: list[str]) -> None:
        if gen != self._generation or self._bus is None:
            return
        log.debug("daemon knows %d device(s)", len(ids))
        pending = set(ids)
        fresh: dict[str, Device] = {}
        if not ids:
            self._devices = {}
            self.devicesChanged.emit()
            return

        def done(device_id: str) -> None:
            pending.discard(device_id)
            if not pending and gen == self._generation:
                self._devices = {i: fresh[i] for i in ids if i in fresh}
                self.devicesChanged.emit()
                for d in self._devices.values():
                    if d.reachable and d.trusted:
                        self._fetch_battery(d.id)
                        self._sms.device_ready(d.id)

        for device_id in ids:
            self._fetch_device(device_id, fresh, done)

    def _fetch_device(
        self, device_id: str, fresh: dict[str, Device], done: Callable[[str], None]
    ) -> None:
        assert self._bus is not None
        old = self._devices.get(device_id)

        def got(props: dict[str, Any]) -> None:
            fresh[device_id] = Device(
                id=device_id,
                name=str(props.get("name", "")),
                type=str(props.get("type", "phone")),
                reachable=bool(props.get("isReachable", False)),
                trusted=bool(props.get("isPaired", False)),
                links=link_names(props.get("activeProviderNames")),
                battery=old.battery if old else None,
                charging=old.charging if old else False,
            )
            done(device_id)

        def failed(_name: str, _text: str) -> None:
            done(device_id)  # vanished between devices() and GetAll; just skip it

        self._bus.get_all(self._addr(device_id, None, P.DEVICE_IFACE), got, failed)

    def _on_daemon_unavailable(self, name: str, _text: str) -> None:
        log.warning("kdeconnectd not available: %s", name)
        self._set_all_unreachable()
        self.error.emit("KDE Connect daemon (kdeconnectd) is not running")

    # --- signals -------------------------------------------------------------

    def _on_daemon_signal(self, msg: Message) -> None:
        if _member(msg) in {
            "deviceAdded",
            "deviceRemoved",
            "deviceVisibilityChanged",
            "deviceListChanged",
        }:
            self._reload_timer.start()

    def _on_device_signal(self, msg: Message) -> None:
        device_id = P.device_id_from_path(_path(msg))
        device = self._devices.get(device_id or "")
        if device is None:
            self._reload_timer.start()
            return
        member, body = _member(msg), msg.body
        if member in ("linksChanged", "reachableChanged"):
            self._refresh_links(device.id)  # which transports (Wi-Fi/Bluetooth) are up
        if member == "reachableChanged":
            reachable = bool(body[0])
            log.info("device %s %s", short(device.id), "reachable" if reachable else "unreachable")
            self._update(device.id, reachable=reachable)
            if reachable and device.trusted:
                self._fetch_battery(device.id)
                self._sms.device_ready(device.id)
        elif member == "pairStateChanged":
            paired = int(body[0]) == P.PairState.PAIRED
            log.info("device %s pair state %d", short(device.id), int(body[0]))
            if paired != device.trusted:
                self._update(device.id, trusted=paired)
                self.devicesChanged.emit()  # pairing changes which devices are usable
                if not paired:
                    self._sms.forget_device(device.id)
                elif device.reachable:
                    self._sms.device_ready(device.id)
        elif member == "nameChanged":
            self._update(device.id, name=str(body[0]))
        elif member == "typeChanged":
            self._update(device.id, type=str(body[0]))
        elif member == "pairingFailed":
            log.info("pairing with %s failed", short(device.id))
            self.error.emit(f"Pairing failed: {body[0]}" if body else "Pairing failed")

    def _on_battery_refreshed(self, msg: Message) -> None:
        device_id = P.device_id_from_path(_path(msg))
        if device_id in self._devices and len(msg.body) >= 2:
            charging, charge = msg.body[0], msg.body[1]
            self._update(device_id, battery=int(charge), charging=bool(charging))

    def _on_conversation_signal(self, msg: Message) -> None:
        device_id = P.device_id_from_path(_path(msg))
        if device_id in self._devices:
            self._sms.on_signal(msg, _member(msg), device_id)

    def _on_owner_changed(self, msg: Message) -> None:
        _name, _old, new = msg.body
        self._owner = new or None
        if new:
            log.info("kdeconnectd (re)started")
            self._sms = self._sms_after_restart()
            self._reload_timer.start()
        else:
            log.warning("kdeconnectd went away")
            self._set_all_unreachable()

    def _on_bus_lost(self) -> None:
        self._set_all_unreachable()
        self.error.emit("Lost the connection to the D-Bus session bus")

    def _sms_after_restart(self) -> SmsChannel:
        # A restarted daemon has an empty cache and no memory of our page requests.
        self._sms.deleteLater()
        return SmsChannel(self)

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _addr(device_id: str, suffix: str | None, interface: str) -> DBusAddress:
        path = P.plugin_path(device_id, suffix) if suffix else P.device_path(device_id)
        return DBusAddress(path, P.SERVICE, interface)

    def _update(self, device_id: str, **changes: Any) -> None:
        device = self._devices.get(device_id)
        if device is None:
            return
        updated = replace(device, **changes)
        if updated != device:
            self._devices[device_id] = updated
            self.deviceChanged.emit(device_id)

    def _refresh_links(self, device_id: str) -> None:
        if self._bus is None:
            return

        def got(props: dict[str, Any]) -> None:
            self._update(device_id, links=link_names(props.get("activeProviderNames")))

        self._bus.get_all(self._addr(device_id, None, P.DEVICE_IFACE), got, lambda _n, _t: None)

    def _set_all_unreachable(self) -> None:
        for device_id in list(self._devices):
            self._update(device_id, reachable=False, links=())

    def _fetch_battery(self, device_id: str) -> None:
        if self._bus is None:
            return

        def got(props: dict[str, Any]) -> None:
            if props.get("hasBattery", True):
                self._update(
                    device_id,
                    battery=int(props.get("charge", -1)),
                    charging=bool(props.get("isCharging", False)),
                )

        self._bus.get_all(
            self._addr(device_id, P.BATTERY_SUFFIX, P.BATTERY_IFACE),
            on_reply=got,
            on_error=lambda name, _t: log.debug("no battery for %s: %s", short(device_id), name),
        )

    def _device_call(
        self,
        device_id: str,
        suffix: str | None,
        interface: str,
        method: str,
        signature: str = "",
        body: tuple[Any, ...] = (),
        failure: str = "",
    ) -> None:
        if self._bus is None:
            self.error.emit("Not connected to KDE Connect")
            return

        def on_error(name: str, _text: str) -> None:
            log.warning("%s on %s failed: %s", method, short(device_id), name)
            self.error.emit(failure or f"{method} failed")

        self._bus.call(
            self._addr(device_id, suffix, interface), method, signature, body, None, on_error
        )

    # --- devices: actions ----------------------------------------------------

    def request_pairing(self, device_id: str) -> None:
        self._device_call(
            device_id, None, P.DEVICE_IFACE, "requestPairing", failure="Couldn't request pairing"
        )

    def unpair(self, device_id: str) -> None:
        self._device_call(device_id, None, P.DEVICE_IFACE, "unpair", failure="Couldn't unpair")

    def ring(self, device_id: str) -> None:
        self._device_call(
            device_id,
            P.FINDMYPHONE_SUFFIX,
            P.FINDMYPHONE_IFACE,
            "ring",
            failure="Couldn't ring the phone",
        )

    # --- sharing -------------------------------------------------------------

    def send_clipboard(self, device_id: str) -> None:
        self._device_call(
            device_id,
            P.CLIPBOARD_SUFFIX,
            P.CLIPBOARD_IFACE,
            "sendClipboard",
            failure="Couldn't send the clipboard",
        )

    def share_url(self, device_id: str, url: str) -> None:
        self._device_call(
            device_id,
            P.SHARE_SUFFIX,
            P.SHARE_IFACE,
            "shareUrl",
            "s",
            (url,),
            failure="Couldn't send the file",
        )

    # --- messages ------------------------------------------------------------

    def conversations(self, device_id: str) -> list[Conversation]:
        return self._sms.conversations(device_id)

    def request_conversation(self, device_id: str, thread_id: int, start: int, end: int) -> None:
        self._sms.request_conversation(device_id, thread_id, start, end)

    def send_message(
        self,
        device_id: str,
        addresses: tuple[str, ...],
        text: str,
        thread_id: int | None = None,
    ) -> None:
        self._sms.send(device_id, addresses, text, thread_id)

    def request_attachment(self, device_id: str, attachment: Attachment) -> None:
        self._sms.request_attachment(device_id, attachment)

    # --- phone notifications (not yet) ---------------------------------------

    def notifications(self, device_id: str) -> list[Notification]:
        return []

    def dismiss_notification(self, device_id: str, notification_id: str) -> None:
        log.debug("dismiss_notification: phone notifications are not implemented yet")
