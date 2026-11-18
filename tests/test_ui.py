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
