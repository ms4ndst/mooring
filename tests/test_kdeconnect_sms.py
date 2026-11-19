"""SmsChannel through KdeConnectBackend, against a scripted daemon. Synthetic data only."""

from datetime import datetime, timedelta

import pytest

from mooring.backend import dbus_paths as P
from mooring.backend.kdeconnect import KdeConnectBackend
from mooring.backend.sms_decode import SIGNATURE
from tests.fakes.fake_bus import FakeBus
from tests.test_kdeconnect_backend import PHONE, add_device

T0 = datetime(2026, 1, 1, 12, 0)
ALEX, SAM = "+46700000001", "+46700000002"


def variant(uid, thread, body, minutes, addr, kind=1, read=1, attachments=()):
    date_ms = int((T0 + timedelta(minutes=minutes)).timestamp() * 1000)
    return (
        SIGNATURE,
        (1, body, [(addr,)], date_ms, kind, read, thread, uid, 1, list(attachments)),
    )


class ScriptedPhone:
    """Daemon-side conversation cache, emitting signals the way kdeconnectd does."""

    def __init__(self, bus: FakeBus):
        self.bus = bus
        # thread -> list of variants, oldest first
        self.threads = {
            1: [variant(i, 1, f"alex {i}", i, ALEX, kind=1 if i % 2 else 2) for i in range(1, 121)],
            2: [variant(500, 2, "sam unread", 30, SAM, read=0)],
        }
        self.cache_only = True  # True: no conversationLoaded (served from cache)
        conv = (P.device_path(PHONE), P.CONVERSATIONS_IFACE)
        bus.methods[(*conv, "activeConversations")] = lambda: (
            [msgs[-1] for msgs in self.threads.values()],
        )
        bus.methods[(*conv, "requestAllConversationThreads")] = lambda: ()
        bus.methods[(*conv, "requestConversation")] = self.request
        bus.methods[(*conv, "replyToConversation")] = lambda *a: ()
        bus.methods[(*conv, "sendWithoutConversation")] = lambda *a: ()
        bus.methods[(*conv, "requestAttachmentFile")] = self.attachment

    def emit(self, member, sig, body):
        self.bus.emit(P.device_path(PHONE), P.CONVERSATIONS_IFACE, member, sig, body)

    def request(self, thread, start, end):
        msgs = self.threads.get(thread, [])
        newest_first = list(reversed(msgs))[start:end]
        for v in newest_first:
            self.emit("conversationUpdated", "v", (v,))
        if not self.cache_only:
            self.emit("conversationLoaded", "xt", (thread, len(msgs)))
        return ()

    def attachment(self, part_id, unique_id):
        self.emit("attachmentReceived", "ss", (f"/tmp/fake-{unique_id}", unique_id))
        return ()


@pytest.fixture
def bus():
    bus = FakeBus()
    bus.methods[(P.DAEMON_PATH, P.DAEMON_IFACE, "devices")] = lambda r, p: ([PHONE],)
    add_device(bus, PHONE, "Test Phone")
    return bus


@pytest.fixture
def phone(bus):
    return ScriptedPhone(bus)


@pytest.fixture
def backend(qtbot, bus, phone):
    b = KdeConnectBackend(client_factory=lambda: bus)
    with qtbot.waitSignal(b.conversationsChanged):
        b.start()
    return b


def test_conversations_load_on_connect(backend, bus):
    convs = {c.thread_id: c for c in backend.conversations(PHONE)}
    assert set(convs) == {1, 2}
    assert convs[2].unread and not convs[1].unread  # 1's newest is outgoing
    assert convs[2].display_name == SAM  # no contacts synced -> number
    assert bus.called("requestAllConversationThreads")  # sync asked once


def test_full_page_flushes_immediately(qtbot, backend):
    with qtbot.waitSignal(backend.messagesLoaded, timeout=100) as blocker:
        backend.request_conversation(PHONE, 1, 0, 50)
    dev, thread, start, msgs = blocker.args
    assert (dev, thread, start, len(msgs)) == (PHONE, 1, 0, 50)
    assert [m.id for m in msgs] == list(range(71, 121))  # oldest first
    assert msgs[-1].outgoing  # uid 120 is kind 2


def test_short_page_flushes_after_quiet_period(qtbot, backend):
    with qtbot.waitSignal(backend.messagesLoaded, timeout=2000) as blocker:
        backend.request_conversation(PHONE, 1, 100, 150)  # only 20 left
    assert len(blocker.args[3]) == 20 and blocker.args[2] == 100


def test_conversation_loaded_then_quiet(qtbot, backend, phone):
    phone.cache_only = False
    with qtbot.waitSignal(backend.messagesLoaded, timeout=2000) as blocker:
        backend.request_conversation(PHONE, 2, 0, 50)
    assert [m.id for m in blocker.args[3]] == [500]


def test_never_asks_beyond_reported_total(qtbot, backend, bus, phone):
    phone.cache_only = False
    with qtbot.waitSignal(backend.messagesLoaded, timeout=2000):
        backend.request_conversation(PHONE, 2, 0, 50)
    calls = len(bus.called("requestConversation"))
    with qtbot.waitSignal(backend.messagesLoaded, timeout=100) as blocker:
        backend.request_conversation(PHONE, 2, 5, 55)
    assert blocker.args[3] == [] and len(bus.called("requestConversation")) == calls


def test_opening_thread_marks_it_read(qtbot, backend):
    with qtbot.waitSignal(backend.conversationsChanged):
        backend.request_conversation(PHONE, 2, 0, 50)
    assert not next(c for c in backend.conversations(PHONE) if c.thread_id == 2).unread


def test_new_incoming_message(qtbot, backend, phone):
    with (
        qtbot.waitSignal(backend.messageAdded) as added,
        qtbot.waitSignal(backend.conversationsChanged),
    ):
        phone.emit("conversationUpdated", "v", (variant(501, 2, "again", 40, SAM, read=0),))
    assert added.args[1].id == 501
    latest = next(c for c in backend.conversations(PHONE) if c.thread_id == 2).last_message
    assert latest.id == 501


def test_replayed_old_messages_are_not_new(qtbot, backend, phone):
    with qtbot.assertNotEmitted(backend.messageAdded):
        phone.emit("conversationUpdated", "v", (phone.threads[1][5],))


def test_spoofed_conversation_signal_ignored(qtbot, backend, bus):
    with qtbot.assertNotEmitted(backend.messageAdded):
        bus.emit(
            P.device_path(PHONE),
            P.CONVERSATIONS_IFACE,
            "conversationCreated",
            "v",
            (variant(900, 9, "spoof", 99, "+1"),),
            sender=":1.99",
        )
    assert not any(c.thread_id == 9 for c in backend.conversations(PHONE))


def test_reply_and_new_message_signatures(backend, bus):
    backend.send_message(PHONE, (ALEX,), "hi", 1)
    backend.send_message(PHONE, ("+46700000077",), "hello", None)
    reply = bus.called("replyToConversation")[0]
    assert reply[3] == (1, "hi", []) and reply[4] == "xsav"
    new = bus.called("sendWithoutConversation")[0]
    assert new[3] == ([("(s)", ("+46700000077",))], "hello", []) and new[4] == "avsav"


def test_reply_to_unknown_thread_falls_back_to_addresses(backend, bus):
    backend.send_message(PHONE, (ALEX,), "hi", 999)
    assert bus.called("sendWithoutConversation") and not bus.called("replyToConversation")


def test_attachment_download(qtbot, backend):
    from mooring.backend.base import Attachment

    with qtbot.waitSignal(backend.attachmentReady) as blocker:
        backend.request_attachment(PHONE, Attachment(3, "image/jpeg", "u3"))
    assert blocker.args == [PHONE, "/tmp/fake-u3"]


def test_contact_names_from_synced_vcards(qtbot, backend, tmp_path, monkeypatch):
    from mooring.services.contacts import vcard_dir

    d = vcard_dir(PHONE)
    d.mkdir(parents=True)
    (d / "x.vcf").write_text(f"BEGIN:VCARD\nFN:Sam Sample\nTEL:{SAM}\nEND:VCARD\n")
    convs = {c.thread_id: c for c in backend.conversations(PHONE)}
    assert convs[2].display_name == "Sam Sample"


@pytest.fixture
def silent_phone(bus, monkeypatch):
    """A phone that never answers the sync (cache empty, no signals)."""
    import mooring.backend.kdeconnect_sms as sms

    monkeypatch.setattr(sms, "SYNC_WAIT_MS", 50)
    phone = ScriptedPhone(bus)
    phone.threads = {}
    return phone


@pytest.mark.parametrize(
    ("providers", "expected"),
    [
        (["Bluetooth (beta)"], "Bluetooth"),
        (["LAN", "Bluetooth (beta)"], "Bluetooth"),
        (["LAN"], "SMS permission"),
    ],
    ids=["bluetooth-only", "wifi-and-bluetooth", "wifi"],
)
def test_unanswered_sync_is_explained(qtbot, bus, silent_phone, providers, expected):
    bus.props[(P.device_path(PHONE), P.DEVICE_IFACE)]["activeProviderNames"] = providers
    b = KdeConnectBackend(client_factory=lambda: bus)
    with qtbot.waitSignal(b.error, timeout=1000) as blocker:
        b.start()
    assert expected in blocker.args[0]


def test_sync_answer_cancels_the_warning(qtbot, bus, silent_phone):
    b = KdeConnectBackend(client_factory=lambda: bus)
    b.start()
    silent_phone.emit("conversationCreated", "v", (variant(1, 1, "hi", 1, ALEX),))
    with qtbot.assertNotEmitted(b.error, wait=150):
        pass


def test_cached_conversations_mean_no_warning(qtbot, bus, monkeypatch):
    import mooring.backend.kdeconnect_sms as sms

    monkeypatch.setattr(sms, "SYNC_WAIT_MS", 50)
    ScriptedPhone(bus)  # has cached threads
    b = KdeConnectBackend(client_factory=lambda: bus)
    b.start()
    with qtbot.assertNotEmitted(b.error, wait=150):
        pass
