"""NotificationChannel through KdeConnectBackend, against a scripted daemon. Synthetic data."""

import pytest

from mooring.backend import dbus_paths as P
from mooring.backend.kdeconnect import KdeConnectBackend
from mooring.backend.kdeconnect_notifications import NotificationChannel
from tests.fakes.fake_bus import FakeBus
from tests.test_kdeconnect_backend import PHONE, add_device

LIST = P.plugin_path(PHONE, P.NOTIFICATIONS_SUFFIX)


def item_path(public_id):
    return f"{LIST}/{public_id}"


class ScriptedNotifications:
    def __init__(self, bus: FakeBus):
        self.bus = bus
        self.active: dict[str, dict] = {}
        bus.methods[(LIST, P.NOTIFICATIONS_IFACE, "activeNotifications")] = lambda: (
            list(self.active),
        )

    def add(self, public_id, emit=True, **props):
        data = {
            "appName": "Chat",
            "title": "Sam Placeholder",
            "text": "Lunch?",
            "ticker": "",
            "dismissable": True,
            "replyId": "",
            "hasIcon": False,
            "iconPath": "",
            "silent": False,
        } | props
        self.active[public_id] = data
        self.bus.props[(item_path(public_id), P.NOTIFICATION_IFACE)] = data
        for method in ("dismiss",):
            self.bus.methods[(item_path(public_id), P.NOTIFICATION_IFACE, method)] = (
                lambda pid=public_id: self.remove(pid) or ()
            )
        self.bus.methods[(item_path(public_id), P.NOTIFICATION_IFACE, "sendReply")] = lambda text: ()
        if emit:
            self.bus.emit(LIST, P.NOTIFICATIONS_IFACE, "notificationPosted", "s", (public_id,))

    def remove(self, public_id):
        self.active.pop(public_id, None)
        self.bus.props.pop((item_path(public_id), P.NOTIFICATION_IFACE), None)
        self.bus.emit(LIST, P.NOTIFICATIONS_IFACE, "notificationRemoved", "s", (public_id,))


@pytest.fixture
def bus():
    bus = FakeBus()
    bus.methods[(P.DAEMON_PATH, P.DAEMON_IFACE, "devices")] = lambda r, p: ([PHONE],)
    add_device(bus, PHONE, "Test Phone")
    return bus


@pytest.fixture
def phone(bus):
    n = ScriptedNotifications(bus)
    n.add("1", emit=False, appName="Calendar", title="Standup", text="in 10 min")
    n.add("2", emit=False, replyId="r-2")
    return n


@pytest.fixture
def backend(qapp, bus, phone):
    b = KdeConnectBackend(client_factory=lambda: bus)
    b.start()
    return b


def by_id(backend):
    return {n.id: n for n in backend.notifications(PHONE)}


def test_active_notifications_load_on_connect(backend):
    ns = by_id(backend)
    assert set(ns) == {"1", "2"}
    assert ns["1"].app_name == "Calendar" and not ns["1"].repliable
    assert ns["2"].repliable and ns["2"].dismissable


def test_posted_updated_removed(qtbot, backend, phone, bus):
    with qtbot.waitSignal(backend.notificationPosted) as posted:
        phone.add("3", title="New", text="hello")
    assert posted.args[1].id == "3"
    first_seen = by_id(backend)["3"].timestamp

    bus.props[(item_path("3"), P.NOTIFICATION_IFACE)]["text"] = "hello again"
    with qtbot.waitSignal(backend.notificationPosted):
        bus.emit(LIST, P.NOTIFICATIONS_IFACE, "notificationUpdated", "s", ("3",))
    assert by_id(backend)["3"].text == "hello again"
    assert by_id(backend)["3"].timestamp == first_seen  # keeps its place

    with qtbot.waitSignal(backend.notificationRemoved) as removed:
        phone.remove("3")
    assert removed.args == [PHONE, "3"]
    assert "3" not in by_id(backend)


def test_ticker_used_when_text_empty(backend, phone):
    phone.add("4", text="", ticker="Sam: hi")
    assert by_id(backend)["4"].text == "Sam: hi"


def test_icon_arrives_with_ready_signal(backend, phone, bus):
    bus.props[(item_path("1"), P.NOTIFICATION_IFACE)] |= {"hasIcon": True, "iconPath": "/x.png"}
    bus.emit(item_path("1"), P.NOTIFICATION_IFACE, "ready")
    assert by_id(backend)["1"].icon_path == "/x.png"


def test_all_removed_and_disconnect(qtbot, backend, bus):
    bus.emit(LIST, P.NOTIFICATIONS_IFACE, "allNotificationsRemoved")
    assert backend.notifications(PHONE) == []


def test_unreachable_clears_list(backend, bus):
    bus.emit(P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (False,))
    assert backend.notifications(PHONE) == []


def test_dismiss_and_reply_call_the_item_object(backend, bus):
    backend.reply_notification(PHONE, "2", "On my way")
    backend.dismiss_notification(PHONE, "1")
    reply = bus.called("sendReply")[0]
    assert reply[0] == item_path("2") and reply[3] == ("On my way",) and reply[4] == "s"
    assert bus.called("dismiss")[0][0] == item_path("1")
    assert "1" not in by_id(backend)  # daemon emitted notificationRemoved


def test_spoofed_notification_signal_ignored(backend, phone, bus):
    phone.active["9"] = {}
    bus.props[(item_path("9"), P.NOTIFICATION_IFACE)] = {"title": "spoof"}
    bus.emit(LIST, P.NOTIFICATIONS_IFACE, "notificationPosted", "s", ("9",), sender=":1.99")
    assert "9" not in by_id(backend)


def test_public_id_from_path():
    assert NotificationChannel.public_id_from_path(item_path("abc")) == "abc"
    assert NotificationChannel.public_id_from_path(LIST) is None
