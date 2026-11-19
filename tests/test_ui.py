from mooring.app import ensure_active_device
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
