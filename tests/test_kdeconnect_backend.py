"""KdeConnectBackend against a scripted bus. All IDs and names are synthetic."""

import pytest

from mooring.backend import dbus_paths as P
from mooring.backend.kdeconnect import KdeConnectBackend
from tests.fakes.fake_bus import FakeBus

PHONE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TABLET = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def add_device(bus: FakeBus, device_id: str, name: str, reachable=True, paired=True, charge=80):
    bus.props[(P.device_path(device_id), P.DEVICE_IFACE)] = {
        "name": name,
        "type": "phone",
        "isReachable": reachable,
        "isPaired": paired,
        "pairState": P.PairState.PAIRED if paired else P.PairState.NOT_PAIRED,
    }
    bus.props[(P.plugin_path(device_id, P.BATTERY_SUFFIX), P.BATTERY_IFACE)] = {
        "charge": charge,
        "isCharging": False,
        "hasBattery": True,
    }
    for method in ("requestPairing", "unpair"):
        bus.methods[(P.device_path(device_id), P.DEVICE_IFACE, method)] = lambda: ()
    bus.methods[(P.plugin_path(device_id, P.FINDMYPHONE_SUFFIX), P.FINDMYPHONE_IFACE, "ring")] = (
        lambda: ()
    )


@pytest.fixture
def bus():
    bus = FakeBus()
    ids = [PHONE, TABLET]
    bus.methods[(P.DAEMON_PATH, P.DAEMON_IFACE, "devices")] = lambda reachable, paired: (list(ids),)
    add_device(bus, PHONE, "Test Phone")
    add_device(bus, TABLET, "Test Tablet", reachable=False, paired=False)
    bus.ids = ids  # let tests change the daemon's device list
    return bus


@pytest.fixture
def backend(qapp, bus):
    b = KdeConnectBackend(client_factory=lambda: bus)
    b.start()
    return b


def test_start_lists_devices_with_battery(backend, bus):
    devices = {d.id: d for d in backend.devices()}
    assert set(devices) == {PHONE, TABLET}
    phone = devices[PHONE]
    assert phone.reachable and phone.trusted and phone.name == "Test Phone"
    assert phone.battery == 80
    tablet = devices[TABLET]
    assert not tablet.reachable and not tablet.trusted and tablet.battery is None
    assert bus.called("devices")[0][3] == (False, False)


def test_reachability_signal_flips_device(qtbot, backend, bus):
    with qtbot.waitSignal(backend.deviceChanged) as blocker:
        bus.emit(P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (False,))
    assert blocker.args == [PHONE]
    assert not backend.device(PHONE).reachable

    bus.props[(P.plugin_path(PHONE, P.BATTERY_SUFFIX), P.BATTERY_IFACE)]["charge"] = 55
    bus.emit(P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (True,))
    assert backend.device(PHONE).reachable
    assert backend.device(PHONE).battery == 55  # refetched on reconnect


def test_battery_refreshed_signal(backend, bus):
    bus.emit(P.plugin_path(PHONE, P.BATTERY_SUFFIX), P.BATTERY_IFACE, "refreshed", "bi", (True, 42))
    phone = backend.device(PHONE)
    assert phone.battery == 42 and phone.charging


def test_pair_state_changes(qtbot, backend, bus):
    with qtbot.waitSignal(backend.devicesChanged):
        bus.emit(
            P.device_path(TABLET), P.DEVICE_IFACE, "pairStateChanged", "i", (P.PairState.PAIRED,)
        )
    assert backend.device(TABLET).trusted
    bus.emit(P.device_path(TABLET), P.DEVICE_IFACE, "pairStateChanged", "i", (0,))
    assert not backend.device(TABLET).trusted


def test_signals_for_other_devices_are_routed_by_path(backend, bus):
    bus.emit(P.device_path(TABLET), P.DEVICE_IFACE, "nameChanged", "s", ("Renamed",))
    assert backend.device(TABLET).name == "Renamed"
    assert backend.device(PHONE).name == "Test Phone"


def test_device_added_triggers_debounced_reload(qtbot, backend, bus):
    new = "cccccccccccccccccccccccccccccccc"
    add_device(bus, new, "New Phone")
    bus.ids.append(new)
    with qtbot.waitSignal(backend.devicesChanged, timeout=2000):
        bus.emit(P.DAEMON_PATH, P.DAEMON_IFACE, "deviceAdded", "s", (new,))
    assert backend.device(new) is not None


def test_daemon_exit_marks_everything_unreachable(backend, bus):
    bus.emit(
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        "sss",
        (P.SERVICE, ":1.7", ""),
        sender="org.freedesktop.DBus",
    )
    assert not any(d.reachable for d in backend.devices())


def test_other_name_owner_changes_are_ignored(backend, bus):
    bus.emit(
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        "sss",
        ("org.example.Other", ":1.7", ""),
        sender="org.freedesktop.DBus",
    )
    assert backend.device(PHONE).reachable


def test_daemon_not_running_reports_error(qtbot, qapp):
    bus = FakeBus()
    bus.daemon_running = False
    backend = KdeConnectBackend(client_factory=lambda: bus)
    with qtbot.waitSignal(backend.error) as blocker:
        backend.start()
    assert "kdeconnectd" in blocker.args[0]
    assert backend.devices() == []


def test_actions_call_the_right_objects(backend, bus):
    backend.ring(PHONE)
    backend.request_pairing(TABLET)
    backend.unpair(PHONE)
    assert bus.called("ring")[0][:2] == (
        P.plugin_path(PHONE, P.FINDMYPHONE_SUFFIX),
        P.FINDMYPHONE_IFACE,
    )
    assert bus.called("requestPairing")[0][0] == P.device_path(TABLET)
    assert bus.called("unpair")[0][0] == P.device_path(PHONE)


def test_failed_action_emits_error(qtbot, backend):
    with qtbot.waitSignal(backend.error):
        backend.send_clipboard(PHONE)  # no clipboard method scripted -> D-Bus error


def test_device_id_from_path():
    assert P.device_id_from_path(P.plugin_path(PHONE, "battery")) == PHONE
    assert P.device_id_from_path(P.device_path(PHONE)) == PHONE
    assert P.device_id_from_path("/modules/kdeconnect") is None


def test_spoofed_signals_are_ignored(backend, bus):
    # Any session process can emit a look-alike signal; only the daemon's own count.
    bus.emit(
        P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (False,), sender=":1.99"
    )
    bus.emit(
        P.plugin_path(PHONE, P.BATTERY_SUFFIX),
        P.BATTERY_IFACE,
        "refreshed",
        "bi",
        (False, 1),
        sender=":1.99",
    )
    phone = backend.device(PHONE)
    assert phone.reachable and phone.battery == 80


def test_fake_name_owner_changed_is_ignored(backend, bus):
    bus.emit(
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        "sss",
        (P.SERVICE, ":1.42", ":1.99"),
        sender=":1.99",  # not the bus daemon
    )
    bus.emit(
        P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (False,), sender=":1.99"
    )
    assert backend.device(PHONE).reachable


def test_daemon_restart_moves_trust_to_new_owner(backend, bus):
    bus.emit(
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        "sss",
        (P.SERVICE, ":1.42", ":1.77"),
        sender="org.freedesktop.DBus",
    )
    bus.emit(
        P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (False,), sender=":1.42"
    )
    assert backend.device(PHONE).reachable  # old owner no longer trusted
    bus.emit(
        P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (False,), sender=":1.77"
    )
    assert not backend.device(PHONE).reachable


def test_links_are_tracked(backend, bus):
    props = bus.props[(P.device_path(PHONE), P.DEVICE_IFACE)]
    props["activeProviderNames"] = ["Bluetooth (beta)"]
    bus.emit(P.device_path(PHONE), P.DEVICE_IFACE, "linksChanged", sender=":1.42")
    assert backend.device(PHONE).links == ("Bluetooth",)

    props["activeProviderNames"] = ["Bluetooth (beta)", "LAN"]
    bus.emit(P.device_path(PHONE), P.DEVICE_IFACE, "linksChanged", sender=":1.42")
    assert backend.device(PHONE).links == ("Wi-Fi", "Bluetooth")

    bus.emit(
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        "sss",
        (P.SERVICE, ":1.42", ""),
        sender="org.freedesktop.DBus",
    )
    assert backend.device(PHONE).links == ()


SFTP = P.plugin_path(PHONE, P.SFTP_SUFFIX)
ROOT = f"/run/user/1000/{PHONE}/storage/emulated/0"


def test_mount_storage_picks_internal_storage(qtbot, backend, bus):
    bus.methods[(SFTP, P.SFTP_IFACE, "mountAndWait")] = lambda: (True,)
    bus.methods[(SFTP, P.SFTP_IFACE, "getDirectories")] = lambda: (
        {"/run/sd": ("s", "SD card"), ROOT: ("s", "Internal shared storage")},
    )
    with qtbot.waitSignal(backend.storageReady) as blocker:
        backend.mount_storage(PHONE)
    assert blocker.args == [PHONE, ROOT]
    assert backend.storage_root(PHONE) == ROOT
    bus.emit(P.device_path(PHONE), P.DEVICE_IFACE, "reachableChanged", "b", (False,))
    assert backend.storage_root(PHONE) is None  # mount gone with the link


def test_mount_failure_reports_daemon_reason(qtbot, backend, bus):
    bus.methods[(SFTP, P.SFTP_IFACE, "mountAndWait")] = lambda: (False,)
    bus.methods[(SFTP, P.SFTP_IFACE, "mountPoint")] = lambda: (f"/run/user/1000/{PHONE}",)
    bus.methods[(SFTP, P.SFTP_IFACE, "getMountError")] = lambda: ("sshfs not found",)
    backend.clear_stale_mount = lambda path: False  # nothing stale to repair
    with qtbot.waitSignal(backend.storageFailed) as failed:
        backend.mount_storage(PHONE)
    assert "sshfs not found" in failed.args[1]
    assert backend.storage_root(PHONE) is None


def test_dead_mount_is_repaired_and_mount_retried(qtbot, backend, bus):
    attempts = []

    def mount_and_wait():
        attempts.append(1)
        return (len(attempts) > 1,)  # fails until the dead mount is gone

    cleared = []
    bus.methods[(SFTP, P.SFTP_IFACE, "mountAndWait")] = mount_and_wait
    bus.methods[(SFTP, P.SFTP_IFACE, "mountPoint")] = lambda: (f"/run/user/1000/{PHONE}",)
    bus.methods[(SFTP, P.SFTP_IFACE, "getDirectories")] = lambda: ({ROOT: ("s", "Internal")},)
    backend.clear_stale_mount = lambda path: cleared.append(path) or True
    with qtbot.waitSignal(backend.storageReady) as ready:
        backend.mount_storage(PHONE)
    assert ready.args == [PHONE, ROOT]
    assert cleared == [f"/run/user/1000/{PHONE}"] and len(attempts) == 2


def test_repair_is_tried_only_once(qtbot, backend, bus):
    attempts = []
    bus.methods[(SFTP, P.SFTP_IFACE, "mountAndWait")] = lambda: attempts.append(1) or (False,)
    bus.methods[(SFTP, P.SFTP_IFACE, "mountPoint")] = lambda: ("/run/user/1000/x",)
    bus.methods[(SFTP, P.SFTP_IFACE, "getMountError")] = lambda: ("still broken",)
    backend.clear_stale_mount = lambda path: True  # "repairs" but it keeps failing
    with qtbot.waitSignal(backend.storageFailed):
        backend.mount_storage(PHONE)
    assert len(attempts) == 2  # original + one retry, no loop


def test_clipboard_sync_state_and_toggle(backend, bus):
    state = {"on": True}
    dev = (P.device_path(PHONE), P.DEVICE_IFACE)
    bus.methods[(*dev, "isPluginEnabled")] = lambda name: (state["on"],)

    def set_enabled(name, on):
        assert name == P.CLIPBOARD_PLUGIN
        state["on"] = on
        return ()

    bus.methods[(*dev, "setPluginEnabled")] = set_enabled
    backend.reload_devices()
    assert backend.device(PHONE).clipboard_sync is True
    backend.set_clipboard_sync(PHONE, False)
    assert backend.device(PHONE).clipboard_sync is False
    assert bus.called("setPluginEnabled")[0][4] == "sb"


def test_cable_link_is_named_and_storage_explains_the_limit(qtbot, bus):
    bus.props[(P.device_path(PHONE), P.DEVICE_IFACE)]["activeProviderNames"] = ["LAN"]
    b = KdeConnectBackend(client_factory=lambda: bus)
    b.cable_link_active = lambda: True
    b.start()
    assert b.device(PHONE).links == ("USB cable",)
    with qtbot.waitSignal(b.storageFailed) as failed:
        b.mount_storage(PHONE)
    assert "USB cable" in failed.args[1]
    assert not bus.called("mountAndWait")  # sshfs would dial an unreachable Wi-Fi address


def test_self_identity(qtbot, backend, bus):
    bus.methods[(P.DAEMON_PATH, P.DAEMON_IFACE, "selfId")] = lambda: ("c" * 32,)
    bus.methods[(P.DAEMON_PATH, P.DAEMON_IFACE, "announcedName")] = lambda: ("Test Laptop",)
    got = []
    backend.self_identity(lambda i, n: got.append((i, n)))
    qtbot.waitUntil(lambda: bool(got))
    assert got == [("c" * 32, "Test Laptop")]
