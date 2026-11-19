"""MirrorBackend with a scripted runner. Synthetic serials, apps and output only."""

import pytest

from mooring.backend.mirror import (
    AdbDevice,
    MirrorBackend,
    PhoneApp,
    parse_adb_devices,
    parse_app_list,
    pick_adb_device,
    scrcpy_supports_apps,
)
from tests.fakes.fake_runner import APP_LIST


def test_parse_adb_devices():
    text = (
        "* daemon not running; starting now at tcp:5037\n"
        "List of devices attached\n"
        "AAA111   device usb:3-7 product:acme model:X1 device:X1 transport_id:1\n"
        "192.168.1.20:41235   device product:acme model:X1 transport_id:2\n"
        "BBB222   unauthorized usb:3-8 transport_id:3\n\n"
    )
    a, b, c = parse_adb_devices(text)
    assert (a.serial, a.state, a.model, a.wireless, a.usable) == (
        "AAA111",
        "device",
        "X1",
        False,
        True,
    )
    assert b.wireless and b.label == "X1 · Wi-Fi"
    assert not c.usable and c.label == "Android device · USB (unauthorized)"


def test_parse_app_list_orders_own_apps_first():
    apps = parse_app_list(APP_LIST + "garbage line\n - NoPackage   \n")
    assert [a.name for a in apps] == ["Example Chat", "Maps Sample", "Camera", "Settings"]
    assert apps[0] == PhoneApp("Example Chat", "org.example.chat", False)
    assert apps[2].system


def test_scrcpy_version_gate():
    assert scrcpy_supports_apps("scrcpy 5.0.1")
    assert scrcpy_supports_apps("scrcpy 3.0")
    assert not scrcpy_supports_apps("scrcpy 2.7")
    assert not scrcpy_supports_apps("")


def test_pick_adb_device():
    usb = AdbDevice("AAA", "device", "X1")
    other = AdbDevice("BBB", "device", "Pixel_8")
    locked = AdbDevice("CCC", "unauthorized")
    assert pick_adb_device([usb, locked]) == usb  # the only usable one
    assert pick_adb_device([other, usb], "Acme X1 Pro") == usb  # model match
    assert pick_adb_device([usb, other], "x", preferred="BBB") == other  # saved choice
    assert pick_adb_device([locked]) is None


@pytest.fixture
def mirror(qapp, fake_tools):
    m = MirrorBackend()
    m.detect_tools()
    return m


def test_detects_tools_and_version(qtbot, mirror):
    qtbot.waitUntil(lambda: mirror.tools.scrcpy_version == "scrcpy 5.0.1", timeout=1000)
    assert mirror.tools.ready


def test_devices_and_apps(qtbot, mirror):
    with qtbot.waitSignal(mirror.devicesChanged):
        mirror.refresh_devices()
    (dev,) = mirror.devices()
    assert dev.serial == "FAKESERIAL01" and dev.model == "Pixel_Fake"
    with qtbot.waitSignal(mirror.appsLoaded) as blocker:
        mirror.load_apps(dev.serial)
    assert [a.package for a in blocker.args[1]][:2] == ["org.example.chat", "org.example.maps"]


def test_mirror_and_app_windows(qtbot, mirror, fake_tools):
    qtbot.waitUntil(lambda: bool(mirror.tools.scrcpy_version), timeout=1000)
    with qtbot.waitSignal(mirror.windowsChanged):
        mirror.mirror("FAKESERIAL01", "Phone · Mooring")
    mirror.launch_app("FAKESERIAL01", PhoneApp("Example Chat", "org.example.chat", False))
    (args1, p1), (args2, p2) = fake_tools.spawned
    assert args1 == ("-s", "FAKESERIAL01", "--window-title", "Phone · Mooring", "--stay-awake")
    assert args2 == (
        "-s",
        "FAKESERIAL01",
        "--new-display",
        "--start-app=org.example.chat",
        "--window-title",
        "Example Chat",
    )
    assert mirror.window_count() == 2

    with qtbot.assertNotEmitted(mirror.error):
        p1.exit(0)  # user closed the window
    with qtbot.waitSignal(mirror.error) as blocker:
        p2.exit(1, "INFO: starting\nERROR: Could not find any ADB device")
    assert blocker.args[0] == "scrcpy: Could not find any ADB device"
    assert mirror.window_count() == 0


def test_close_all(qtbot, mirror, fake_tools):
    mirror.mirror("FAKESERIAL01", "a")
    mirror.mirror("FAKESERIAL01", "b")
    mirror.close_all()
    assert mirror.window_count() == 0


def test_old_scrcpy_cannot_open_single_apps(qtbot, mirror, fake_tools):
    qtbot.waitUntil(lambda: bool(mirror.tools.scrcpy_version), timeout=1000)
    mirror.tools.scrcpy_version = "scrcpy 2.7"
    with qtbot.waitSignal(mirror.error):
        mirror.launch_app("FAKESERIAL01", PhoneApp("X", "org.x", False))
    assert fake_tools.spawned == []


@pytest.mark.parametrize(
    ("pair_out", "connect_out", "ok", "message"),
    [
        (
            (0, "Successfully paired to 1.2.3.4:37099 [guid=x]\n", ""),
            (0, "connected to 1.2.3.4:41235\n", ""),
            True,
            "Connected",
        ),
        (
            (1, "", "error: protocol fault (couldn't read status message): Success\n"),
            None,
            False,
            "protocol fault (couldn't read status message): Success",
        ),
        (
            (0, "Successfully paired to 1.2.3.4:37099\n", ""),
            (1, "failed to connect to 1.2.3.4:41235\n", ""),
            False,
            "failed to connect to 1.2.3.4:41235",
        ),
    ],
    ids=["ok", "wrong-code", "connect-fails"],
)
def test_wifi_pairing(qtbot, mirror, fake_tools, pair_out, connect_out, ok, message):
    fake_tools.responses[("pair",)] = pair_out
    if connect_out:
        fake_tools.responses[("connect",)] = connect_out
    with qtbot.waitSignal(mirror.paired, timeout=1000) as blocker:
        mirror.pair("1.2.3.4:37099", "123456", "1.2.3.4:41235")
    assert blocker.args == [ok, message]
    pair_call = next(c for c in fake_tools.calls if c[1][:1] == ("pair",))
    assert pair_call[1] == ("pair", "1.2.3.4:37099", "123456")


def test_pairing_without_connect_address_relies_on_mdns(qtbot, mirror, fake_tools):
    fake_tools.responses[("pair",)] = (0, "Successfully paired to 1.2.3.4:37099\n", "")
    with qtbot.waitSignal(mirror.paired) as blocker:
        mirror.pair("1.2.3.4:37099", "123456")
    assert blocker.args == [True, "Paired"]
    assert not any(c[1][:1] == ("connect",) for c in fake_tools.calls)


def test_version_number_and_embedded_readiness(qtbot, mirror):
    from mooring.backend.mirror import version_number

    assert version_number("scrcpy 5.0.1 <https://github.com/Genymobile/scrcpy>") == "5.0.1"
    assert version_number("nope") == ""
    qtbot.waitUntil(lambda: bool(mirror.tools.scrcpy_version), timeout=1000)
    assert mirror.tools.server == "/fake/share/scrcpy/scrcpy-server"
    assert mirror.tools.embedded_ready  # PyAV is installed in the dev env
