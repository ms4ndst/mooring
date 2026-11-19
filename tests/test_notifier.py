"""Notifier against a fake notification daemon. Synthetic data only."""

from datetime import datetime, timedelta

import pytest

from mooring.backend.base import Message
from mooring.services.notifier import PATH, SERVICE, Notifier
from tests.fakes.fake_backend import PHONE_ID
from tests.fakes.fake_bus import FakeBus

DAEMON = ":1.50"  # FakeBus owner of org.freedesktop.Notifications


def incoming(thread=1000, body="Lunch?", age=timedelta(seconds=5), outgoing=False):
    return Message(
        id=9000 + thread,
        thread_id=thread,
        addresses=("+46700000001",),
        body=body,
        timestamp=datetime.now() - age,
        outgoing=outgoing,
    )


class Harness:
    def __init__(self, backend, settings, caps=("actions", "body")):
        self.bus = FakeBus()
        self.bus.methods[(PATH, SERVICE, "GetCapabilities")] = lambda: (list(caps),)
        self.next_id = 0

        def notify(*args):
            self.next_id += 1
            return (self.next_id,)

        self.bus.methods[(PATH, SERVICE, "Notify")] = notify
        self.opened: list[tuple[str, int]] = []
        self.viewing: tuple[str, int] | None = None
        self.notifier = Notifier(
            backend,
            settings,
            open_thread=lambda d, t: self.opened.append((d, t)),
            is_viewing=lambda d, t: self.viewing == (d, t),
            client_factory=lambda: self.bus,
        )
        self.notifier.start()

    def notifies(self):
        return self.bus.called("Notify")

    def signal(self, member, sig, body, sender=DAEMON):
        self.bus.emit(PATH, SERVICE, member, sig, body, sender=sender)


@pytest.fixture
def h(backend, settings):
    return Harness(backend, settings)


def test_incoming_message_is_notified(h, backend):
    backend.messageAdded.emit(PHONE_ID, incoming())
    (call,) = h.notifies()
    app, replaces, _icon, title, body, actions, hints, _timeout = call[3]
    assert call[4] == "susssasa{sv}i"
    assert (app, replaces, title, body) == ("Mooring", 0, "Alex Example", "Lunch?")
    assert actions == ["default", "Open", "reply", "Reply"]  # dunst: no inline-reply
    assert hints["category"] == ("s", "im.received")


def test_same_thread_replaces_previous_popup(h, backend):
    backend.messageAdded.emit(PHONE_ID, incoming())
    backend.messageAdded.emit(PHONE_ID, incoming(body="Or dinner?"))
    assert h.notifies()[1][3][1] == 1  # replaces_id of the first


@pytest.mark.parametrize(
    "msg",
    [incoming(outgoing=True), incoming(age=timedelta(minutes=10))],
    ids=["outgoing", "old-history"],
)
def test_not_notified(h, backend, msg):
    backend.messageAdded.emit(PHONE_ID, msg)
    assert h.notifies() == []


def test_not_notified_while_viewing_thread(h, backend):
    h.viewing = (PHONE_ID, 1000)
    backend.messageAdded.emit(PHONE_ID, incoming())
    assert h.notifies() == []


def test_hide_text_setting(h, backend, settings):
    settings.set_notify_show_text(False)
    backend.messageAdded.emit(PHONE_ID, incoming())
    assert h.notifies()[0][3][4] == "New message"


def test_markup_is_escaped_when_supported(backend, settings):
    h = Harness(backend, settings, caps=("actions", "body", "body-markup"))
    backend.messageAdded.emit(PHONE_ID, incoming(body="<b>x</b> & y"))
    assert h.notifies()[0][3][4] == "&lt;b&gt;x&lt;/b&gt; &amp; y"


def test_open_action_from_daemon(h, backend):
    backend.messageAdded.emit(PHONE_ID, incoming())
    h.signal("ActionInvoked", "us", (1, "default"))
    assert h.opened == [(PHONE_ID, 1000)]


def test_forged_signals_are_ignored(h, backend):
    backend.messageAdded.emit(PHONE_ID, incoming())
    sent = []
    backend.send_message = lambda *a: sent.append(a)
    h.signal("ActionInvoked", "us", (1, "default"), sender=":1.99")
    h.signal("NotificationReplied", "us", (1, "evil text"), sender=":1.99")
    h.signal("ActionInvoked", "us", (77, "default"))  # not our notification
    assert h.opened == [] and sent == []


def test_inline_reply_sends_message(backend, settings):
    h = Harness(backend, settings, caps=("actions", "body", "inline-reply"))
    backend.messageAdded.emit(PHONE_ID, incoming())
    assert "inline-reply" in h.notifies()[0][3][5]
    sent = []
    backend.send_message = lambda *a: sent.append(a)
    h.signal("NotificationReplied", "us", (1, "  On my way  "))
    assert sent == [(PHONE_ID, ("+46700000001",), "On my way", 1000)]


def test_closed_notification_forgotten(h, backend):
    backend.messageAdded.emit(PHONE_ID, incoming())
    h.signal("NotificationClosed", "uu", (1, 2))
    h.signal("ActionInvoked", "us", (1, "default"))
    assert h.opened == []
    backend.messageAdded.emit(PHONE_ID, incoming(body="new"))
    assert h.notifies()[1][3][1] == 0  # fresh popup, nothing to replace
