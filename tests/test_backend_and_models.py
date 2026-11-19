from mooring.models import ConversationListModel, MessageListModel, NotificationListModel
from mooring.models.roles import Role
from tests.fakes.fake_backend import PHONE_ID, TABLET_ID, FakeBackend


def test_fake_has_devices(backend):
    ids = {d.id for d in backend.devices()}
    assert ids == {PHONE_ID, TABLET_ID}
    assert backend.device(PHONE_ID).reachable


def test_conversations_sorted_newest_first(backend):
    model = ConversationListModel(backend)
    model.set_device(PHONE_ID)
    stamps = [model.index(r).data(Role.Timestamp) for r in range(model.rowCount())]
    assert model.rowCount() == 6
    assert stamps == sorted(stamps, reverse=True)
    assert any(model.index(r).data(Role.Unread) for r in range(model.rowCount()))


def test_empty_device_has_no_conversations(backend):
    model = ConversationListModel(backend)
    model.set_device(TABLET_ID)
    assert model.rowCount() == 0


def test_messages_load_in_pages(qtbot, backend):
    model = MessageListModel(backend, page_size=50)
    with qtbot.waitSignal(backend.messagesLoaded):
        model.open_thread(PHONE_ID, 1000)
    assert model.rowCount() == 50
    newest = model.index(49).data(Role.Item)

    with qtbot.waitSignal(model.olderLoaded):
        assert model.load_older()
    assert model.rowCount() == 100
    assert model.index(99).data(Role.Item) == newest  # older ones went on top

    with qtbot.waitSignal(model.olderLoaded):
        model.load_older()
    assert model.rowCount() == 120
    assert model.exhausted
    assert not model.load_older()

    stamps = [model.index(r).data(Role.Timestamp) for r in range(model.rowCount())]
    assert stamps == sorted(stamps)


def test_send_appends_to_open_thread(qtbot, backend):
    model = MessageListModel(backend)
    with qtbot.waitSignal(backend.messagesLoaded):
        model.open_thread(PHONE_ID, 1001)
    before = model.rowCount()
    with qtbot.waitSignal(backend.messageAdded):
        backend.send_message(PHONE_ID, ("+46700000002",), "hello", 1001)
    assert model.rowCount() == before + 1
    last = model.index(model.rowCount() - 1).data(Role.Item)
    assert last.outgoing and last.body == "hello"


def test_auto_reply(qtbot, qapp):
    backend = FakeBackend(auto_reply_ms=10)
    backend.send_message(PHONE_ID, ("+46700000002",), "ping", 1001)
    with qtbot.waitSignal(backend.messageAdded, timeout=1000) as blocker:
        pass
    assert not blocker.args[1].outgoing


def test_notifications_post_and_dismiss(backend):
    from datetime import datetime

    from mooring.backend.base import Notification

    model = NotificationListModel(backend)
    model.set_device(PHONE_ID)
    count = model.rowCount()
    backend.post_notification(PHONE_ID, Notification("x", "Test", "T", "body", datetime.now()))
    assert model.rowCount() == count + 1
    assert model.notification(0).id == "x"
    backend.dismiss_notification(PHONE_ID, "x")
    assert model.rowCount() == count


def test_notification_updates_keep_position_and_new_ones_sort_by_time(backend):
    from dataclasses import replace
    from datetime import datetime, timedelta

    from mooring.backend.base import Notification

    model = NotificationListModel(backend)
    model.set_device(PHONE_ID)
    ids = [model.notification(r).id for r in range(model.rowCount())]
    second = model.notification(1)
    backend.post_notification(PHONE_ID, replace(second, text="edited"))
    assert [model.notification(r).id for r in range(model.rowCount())] == ids
    assert model.notification(1).text == "edited"

    older = Notification("old", "App", "T", "x", datetime.now() - timedelta(days=1))
    backend.post_notification(PHONE_ID, older)
    assert model.notification(model.rowCount() - 1).id == "old"
