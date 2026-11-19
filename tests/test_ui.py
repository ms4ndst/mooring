import pytest

from mooring.app import ensure_active_device
from mooring.backend import scrcpy_protocol as sp
from mooring.theme.catppuccin import FLAVORS, Theme
from mooring.ui.main_window import MainWindow
from tests.fakes.fake_backend import PHONE_ID


def make_window(qtbot, backend, settings, theme):
    window = MainWindow(backend, settings, theme)
    qtbot.addWidget(window)
    backend.start()
    window.show()
    return window


def test_every_flavor_renders_a_complete_stylesheet():
    for flavor in FLAVORS:
        assert len(FLAVORS[flavor]) == 26
        css = Theme(flavor).stylesheet()
        assert "$" not in css


def test_nav_switches_pages(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    for row in range(window.nav.count()):
        window.nav.setCurrentRow(row)
        assert window.stack.currentIndex() == row
    window.show_page("Notifications")
    assert window.stack.currentWidget() is window.notifications


def test_open_thread_and_send(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = window.messages
    assert page.conversations.rowCount() == 6
    assert not page.compose.isEnabled()

    with qtbot.waitSignal(page.messages.modelReset):
        page.conv_view.setCurrentIndex(page.conversations.index(0))
    qtbot.waitUntil(lambda: page.messages.rowCount() == 50)
    assert page.compose.isEnabled()
    thread = page.messages.thread_id

    page.compose.setText("hi there")
    page.send_button.click()
    assert page.messages.rowCount() == 51
    assert page.compose.text() == ""
    # conversation list re-sorted, the open thread stays selected
    assert page.conversations.conversation(page.conv_view.currentIndex().row()).thread_id == thread


def test_device_header_tracks_reachability(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    header = window.device_header
    assert header.status.text().startswith("Connected via Wi-Fi")
    backend.set_reachable(PHONE_ID, False)
    assert header.status.text() == "Disconnected"
    assert not header.ring.isEnabled()


def test_ensure_active_device_picks_reachable(backend, settings):
    settings.set_active_device("gone")
    ensure_active_device(backend, settings)
    assert settings.active_device == PHONE_ID


def test_flavor_switch_from_settings(qtbot, backend, settings, theme):
    settings.flavorChanged.connect(theme.set_flavor)
    make_window(qtbot, backend, settings, theme)
    settings.set_flavor("latte")
    assert theme.flavor == "latte"
    assert not theme.is_dark


def test_settings_pair_and_unpair(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = window.stack.widget(window.stack.count() - 1)
    assert page.pair_button.text() == "Unpair"

    page.confirm_unpair = lambda _name: False  # user says no
    page.pair_button.click()
    assert backend.device(PHONE_ID).trusted

    page.confirm_unpair = lambda _name: True
    page.pair_button.click()
    assert not backend.device(PHONE_ID).trusted
    assert page.pair_button.text() == "Pair"
    assert window.device_header.status.text() == "Not paired"
    assert not window.device_header.ring.isEnabled()

    page.pair_button.click()  # fake phone accepts immediately
    assert backend.device(PHONE_ID).trusted
    assert "Connected" in window.device_header.status.text()


def test_backend_errors_reach_the_status_bar(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    backend.error.emit("Something broke")
    assert window.statusBar().currentMessage() == "Something broke"


def test_normalize_number():
    from mooring.ui.widgets.new_message import normalize_number

    assert normalize_number(" +46 70-000 00 01 ") == "+46700000001"
    assert normalize_number("070 000 00 01") == "0700000001"
    assert normalize_number("abc") is None
    assert normalize_number("+1") is None


def test_new_message_to_new_number_opens_its_thread(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = window.messages
    page.ask_new_message = lambda: ("+46700000077", "Hi there")
    page.new_button.click()
    qtbot.waitUntil(lambda: page.current_thread is not None)
    conv = page.conversations.conversation(page.conv_view.currentIndex().row())
    assert conv.addresses == ("+46700000077",)
    qtbot.waitUntil(lambda: page.messages.rowCount() == 1)


def test_new_message_to_existing_number_reuses_thread(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = window.messages
    before = page.conversations.rowCount()
    page.ask_new_message = lambda: ("+46700000002", "Again")
    page.new_button.click()
    qtbot.waitUntil(lambda: page.current_thread == 1001)
    assert page.conversations.rowCount() == before


def test_cancelled_new_message_sends_nothing(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    window.messages.ask_new_message = lambda: None
    with qtbot.assertNotEmitted(backend.messageAdded):
        window.messages.new_button.click()


def test_open_thread_from_notification(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    window.show_page("Settings")
    window.open_thread(PHONE_ID, 1003)
    assert window.stack.currentWidget() is window.messages
    assert window.messages.current_thread == 1003


def test_picture_message_bubble_and_download(qtbot, backend, settings, theme, monkeypatch):
    from PyQt6.QtGui import QDesktopServices

    from mooring.models.roles import Role

    window = make_window(qtbot, backend, settings, theme)
    page = window.messages
    window.open_thread(PHONE_ID, 1001)
    qtbot.waitUntil(lambda: page.messages.rowCount() > 0)
    rows = range(page.messages.rowCount())
    pic_row = next(r for r in rows if page.messages.index(r).data(Role.Item).attachments)
    text_row = next(r for r in rows if not page.messages.index(r).data(Role.Item).attachments)
    delegate = page.msg_view.itemDelegate()
    from PyQt6.QtWidgets import QStyleOptionViewItem

    option = QStyleOptionViewItem()
    option.font = page.msg_view.font()
    pic_h = delegate.sizeHint(option, page.messages.index(pic_row)).height()
    text_h = delegate.sizeHint(option, page.messages.index(text_row)).height()
    assert pic_h > text_h + 100  # thumbnail rendered inside the bubble

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    page.msg_view.clicked.emit(page.messages.index(pic_row))
    qtbot.waitUntil(lambda: len(opened) > 0)
    assert opened[0].toLocalFile().endswith("mooring-fake-fake-sunset.png")


def open_photos(qtbot, window):
    """Show Photos and wait for the list. Callers keep ``window`` referenced, like the app."""
    window.show_page("Photos")
    qtbot.waitUntil(lambda: window.photos.model.rowCount() > 0, timeout=10000)
    return window.photos


def test_photos_page_lists_newest_first(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = open_photos(qtbot, window)
    assert page.model.rowCount() == 24
    mtimes = [page.model.photo(r).mtime for r in range(24)]
    assert mtimes == sorted(mtimes, reverse=True)
    assert all(".thumbnails" not in page.model.photo(r).path for r in range(24))
    qtbot.waitUntil(page.thumbs.idle, timeout=10000)
    assert page.status.text() == "24 newest"


def test_new_photo_appears_after_refresh(qtbot, backend, settings, theme):
    import os
    import time

    window = make_window(qtbot, backend, settings, theme)
    page = open_photos(qtbot, window)
    path = backend.add_photo(PHONE_ID, "IMG_just_taken.jpg")
    os.utime(path, (time.time() + 60, time.time() + 60))
    page.refresh()
    qtbot.waitUntil(lambda: page.model.photo(0).name == "IMG_just_taken.jpg", timeout=5000)


def test_save_to_pictures(qtbot, backend, settings, theme, tmp_path):
    window = make_window(qtbot, backend, settings, theme)
    page = open_photos(qtbot, window)
    target = tmp_path / "Pictures"
    page.save_dir = lambda: target
    page.view.selectAll()
    page.save_button.click()
    qtbot.waitUntil(lambda: page.status.text().startswith("Saved"), timeout=10000)
    assert len(list(target.iterdir())) == 24
    page.view.clearSelection()
    page.view.setCurrentIndex(page.model.index(0))
    page.save_button.click()  # same name again -> "(1)" copy, never overwrite
    qtbot.waitUntil(lambda: len(list(target.iterdir())) == 25, timeout=5000)


def test_photo_drag_carries_local_file_urls(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = open_photos(qtbot, window)
    mime = page.model.mimeData([page.model.index(0), page.model.index(1)])
    assert [u.toLocalFile() for u in mime.urls()] == [
        page.model.photo(0).path,
        page.model.photo(1).path,
    ]


def test_dropped_files_are_sent_but_not_the_phones_own(qtbot, backend, settings, theme, tmp_path):
    from PyQt6.QtCore import QUrl

    window = make_window(qtbot, backend, settings, theme)
    open_photos(qtbot, window)
    mine = tmp_path / "report.pdf"
    mine.write_bytes(b"%PDF")
    phone_photo = QUrl.fromLocalFile(window.photos.model.photo(0).path)
    urls = [
        QUrl.fromLocalFile(str(mine)),
        phone_photo,
        QUrl("https://example.com/x"),
        QUrl.fromLocalFile(str(tmp_path)),
    ]  # a folder
    assert [u.toLocalFile() for u in window.files_to_send(urls)] == [str(mine)]


def test_clipboard_button_and_sync_toggle(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    sent = []
    backend.send_clipboard = lambda d: sent.append(d)
    window.device_header.clipboard.click()
    assert sent == [PHONE_ID]

    page = window.stack.widget(window.stack.count() - 1)
    assert page.clipboard_sync.isChecked()
    page.clipboard_sync.setChecked(False)
    assert backend.device(PHONE_ID).clipboard_sync is False
    assert not window.device_header.clipboard.isEnabled()


def test_dropping_window_during_thumbnail_generation_does_not_hang(qtbot, backend, settings, theme):
    """Regression: a per-object QThreadPool, destroyed by Python's GC (GIL held) while
    Pillow threads still needed the GIL, deadlocked. qtbot only holds a weak ref."""
    import gc

    from PyQt6.QtCore import QThreadPool

    page = open_photos(qtbot, make_window(qtbot, backend, settings, theme))
    assert not page.thumbs.idle()  # thumbnails still being made
    del page
    gc.collect()  # frees the window (and its children) with the GIL held
    assert QThreadPool.globalInstance().waitForDone(10000)


def test_human_size():
    from mooring.ui.pages.settings import human_size

    assert human_size(512) == "512 bytes"
    assert human_size(300 * 1024) == "300.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


def open_apps(qtbot, window):
    window.show_page("Apps")
    qtbot.waitUntil(lambda: window.apps.app_list.count() > 0, timeout=3000)
    return window.apps


def test_apps_page_lists_apps_and_opens_windows(qtbot, backend, settings, theme, fake_tools):
    window = make_window(qtbot, backend, settings, theme)
    page = open_apps(qtbot, window)
    assert page.app_list.item(0).text() == "Example Chat"
    assert page.app_list.item(3).text().endswith("system")
    assert page.status.text() == "2 apps · 2 system"
    assert settings.adb_serial_for(PHONE_ID) == ""  # single device: no need to save a choice

    page.app_list.setCurrentRow(0)
    page.open_button.click()
    page.app_list.setCurrentRow(1)
    page.open_button.click()
    titles = [args[args.index("--window-title") + 1] for args, _ in fake_tools.spawned]
    assert titles == ["Example Chat", "Maps Sample"]
    assert page.windows_label.text() == "2 windows open"
    page.close_windows.click()
    assert page.windows_label.text() == ""


def test_apps_search(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = open_apps(qtbot, window)
    page.filter.setText("maps")
    assert [page.app_list.item(i).text() for i in range(page.app_list.count())] == ["Maps Sample"]
    page.filter.setText("org.example")
    assert page.app_list.count() == 2


@pytest.mark.parametrize(
    ("adb_out", "expected"),
    [
        ("List of devices attached\n\n", "No phone found over ADB"),
        ("List of devices attached\nXYZ  unauthorized usb:1-1\n", "hasn't allowed this computer"),
    ],
    ids=["no-device", "unauthorized"],
)
def test_apps_page_explains_setup(qtbot, backend, settings, theme, fake_tools, adb_out, expected):
    fake_tools.responses[("devices", "-l")] = (0, adb_out, "")
    window = make_window(qtbot, backend, settings, theme)
    window.show_page("Apps")
    qtbot.waitUntil(lambda: expected in window.apps.help.text(), timeout=3000)
    assert window.apps.body.currentIndex() == 0
    assert not window.apps.open_button.isEnabled()


def test_apps_page_without_tools(qtbot, backend, settings, theme, monkeypatch):
    import mooring.backend.mirror as mirror

    monkeypatch.setattr(mirror.shutil, "which", lambda name: None)
    window = make_window(qtbot, backend, settings, theme)
    window.show_page("Apps")
    qtbot.waitUntil(
        lambda: "dnf install android-tools scrcpy" in window.apps.help.text(), timeout=3000
    )
    assert not window.apps.pair_button.isEnabled()


def test_pair_from_apps_page(qtbot, backend, settings, theme, fake_tools):
    fake_tools.responses[("pair",)] = (0, "Successfully paired to 1.2.3.4:37099\n", "")
    window = make_window(qtbot, backend, settings, theme)
    page = open_apps(qtbot, window)
    page.ask_pairing = lambda: ("1.2.3.4:37099", "123456", "")
    page.pair_button.click()
    qtbot.waitUntil(lambda: page.status.text() == "Paired", timeout=2000)


def test_pair_dialog_validation(qtbot):
    from mooring.ui.widgets.pair_adb import PairAdbDialog

    d = PairAdbDialog()
    qtbot.addWidget(d)
    d.pair_address.setText("192.168.1.20:37099")
    d.code.setText("12345")
    assert d.result_value() is None
    d.code.setText("123456")
    assert d.result_value() == ("192.168.1.20:37099", "123456", "")
    d.connect_address.setText("not an address")
    assert d.result_value() is None


def test_notifications_reply_and_clear_all(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    window.show_page("Notifications")
    page = window.notifications
    rows = {page.model.notification(r).id: r for r in range(page.model.rowCount())}

    page.view.setCurrentIndex(page.model.index(rows["n1"]))  # not repliable
    assert not page.reply_row.isVisibleTo(page)
    page.view.setCurrentIndex(page.model.index(rows["n2"]))  # chat: repliable
    assert page.reply_row.isVisibleTo(page)
    assert not page.reply_send.isEnabled()
    page.reply_edit.setText("Sure!")
    page.reply_send.click()
    assert backend.notification_replies == [(PHONE_ID, "n2", "Sure!")]
    assert page.reply_edit.text() == ""

    page.clear_all.click()
    assert page.model.rowCount() == 0
    assert page.empty.isVisibleTo(page) and "Notification sync" in page.empty.text()
    assert not page.clear_all.isEnabled()


def mirror_window(qtbot, backend, settings, theme, server=None, factory=None):
    from mooring.backend.scrcpy_client import MirrorSession
    from tests.fakes.fake_scrcpy import FakeScrcpyServer

    server = server or FakeScrcpyServer(width=100, height=200, frames=3)
    window = make_window(qtbot, backend, settings, theme)
    window.mirror.session_factory = factory or (lambda serial: MirrorSession(server.transport))
    return window, server


def test_mirror_page_starts_when_opened_and_stops_when_left(qtbot, backend, settings, theme):
    window, server = mirror_window(qtbot, backend, settings, theme)
    page = window.mirror_page
    window.show_page("Mirror")
    qtbot.waitUntil(lambda: page.session is not None and page.session.frames >= 1, timeout=5000)
    assert page.body.currentIndex() == 1 and page.toggle_button.text() == "Stop"

    page.home_button.click()
    qtbot.waitUntil(lambda: len(server.control_bytes) >= 28, timeout=3000)
    assert bytes(server.control_bytes[:14]) == sp.keycode(sp.KeyAction.DOWN, sp.Keycode.HOME)

    window.show_page("Apps")  # leaving the page stops the stream
    qtbot.waitUntil(lambda: page.session is None, timeout=5000)


def test_mirror_stop_and_start_buttons(qtbot, backend, settings, theme):
    from mooring.backend.scrcpy_client import MirrorSession
    from tests.fakes.fake_scrcpy import FakeScrcpyServer

    window, _ = mirror_window(
        qtbot,
        backend,
        settings,
        theme,
        factory=lambda serial: MirrorSession(FakeScrcpyServer().transport),
    )
    page = window.mirror_page
    window.show_page("Mirror")
    qtbot.waitUntil(lambda: page.session is not None and page.session.running, timeout=5000)
    page.toggle_button.click()  # Stop
    qtbot.waitUntil(lambda: page.session is None, timeout=5000)
    assert page.toggle_button.text() == "Start" and page.status.text() == "Stopped"
    window.mirror.refresh_devices()  # a device refresh must not restart it after Stop
    qtbot.wait(100)
    assert page.session is None
    page.toggle_button.click()  # Start
    qtbot.waitUntil(lambda: page.session is not None and page.session.running, timeout=5000)


def test_mirror_error_reaches_status_bar(qtbot, backend, settings, theme):
    from mooring.backend.scrcpy_client import MirrorSession

    def broken():
        raise ConnectionError("Device is locked")

    window, _ = mirror_window(
        qtbot, backend, settings, theme, factory=lambda serial: MirrorSession(broken)
    )
    window.show_page("Mirror")
    qtbot.waitUntil(lambda: "Device is locked" in window.mirror_page.status.text(), timeout=5000)
    assert window.statusBar().currentMessage() == "Mirroring stopped: Device is locked"


def test_mirror_pop_out_opens_scrcpy_window(qtbot, backend, settings, theme, fake_tools):
    window, _ = mirror_window(qtbot, backend, settings, theme)
    page = window.mirror_page
    window.show_page("Mirror")
    qtbot.waitUntil(lambda: page.session is not None, timeout=5000)
    page.popout_button.click()
    qtbot.waitUntil(lambda: page.session is None, timeout=5000)
    ((args, _proc),) = fake_tools.spawned
    assert args[args.index("--window-title") + 1] == "Pixel (fake) · Mooring"


def test_mirror_page_explains_setup(qtbot, backend, settings, theme, fake_tools):
    fake_tools.responses[("devices", "-l")] = (0, "List of devices attached\n\n", "")
    window, _ = mirror_window(qtbot, backend, settings, theme)
    window.show_page("Mirror")
    page = window.mirror_page
    qtbot.waitUntil(lambda: "No phone found over ADB" in page.help.text(), timeout=3000)
    assert page.body.currentIndex() == 0 and page.session is None
    assert not page.toggle_button.isEnabled()


def test_mirror_page_without_pyav_offers_pop_out(qtbot, backend, settings, theme, monkeypatch):
    import mooring.ui.pages.mirror as mirror_page

    monkeypatch.setattr(mirror_page, "pyav_available", lambda: False)
    window, _ = mirror_window(qtbot, backend, settings, theme)
    window.show_page("Mirror")
    page = window.mirror_page
    qtbot.waitUntil(lambda: "pip install --user av" in page.help.text(), timeout=3000)
    assert page.session is None and page.popout_button.isEnabled()


def test_settings_autostart_toggle(qtbot, backend, settings, theme):
    from mooring.services.desktop import autostart_enabled

    window = make_window(qtbot, backend, settings, theme)
    page = window.stack.widget(window.stack.count() - 1)
    assert not page.autostart.isChecked()
    page.autostart.setChecked(True)
    assert autostart_enabled()
    page.autostart.setChecked(False)
    assert not autostart_enabled()


def test_sidebar_has_icons(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    assert all(not window.nav.item(r).icon().isNull() for r in range(window.nav.count()))
    theme.set_flavor("latte")  # re-tinted, still there
    assert not window.nav.item(0).icon().isNull()


def test_photos_page_reports_storage_failure(qtbot, backend, settings, theme, monkeypatch):
    window = make_window(qtbot, backend, settings, theme)
    monkeypatch.setattr(
        backend,
        "mount_storage",
        lambda device_id: backend.storageFailed.emit(
            device_id, "Couldn't open the phone's storage"
        ),
    )
    window.show_page("Photos")
    page = window.photos
    qtbot.waitUntil(lambda: page.status.text() == "Couldn't open the phone's storage", timeout=3000)
    assert page.refresh_button.isEnabled()  # not stuck "Opening phone storage…"


def test_photos_refresh_always_remounts(qtbot, backend, settings, theme):
    window = make_window(qtbot, backend, settings, theme)
    page = open_photos(qtbot, window)
    calls = []
    original = backend.mount_storage
    backend.mount_storage = lambda device_id: calls.append(device_id) or original(device_id)
    page.refresh()
    qtbot.waitUntil(lambda: not page._busy, timeout=5000)
    assert calls == [PHONE_ID]
