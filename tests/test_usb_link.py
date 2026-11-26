"""KDE Connect over the USB cable, against fake adb. Synthetic IDs only."""

import json
import shlex

from mooring.services.usb_link import (
    KDECONNECT_PORT,
    PHONE_PORT,
    UsbLink,
    cable_link_active,  # the real one: conftest replaces the module attribute
    identity_packet,
    parse_sftp_ports,
    parse_storage_forwards,
    poke_command,
)
from tests.fakes.fake_backend import PHONE_ID, TABLET_ID
from tests.fakes.fake_runner import FakeRunner

LAPTOP_ID = "c" * 32
HEADER = "  sl  local_address rem_address   st ...\n"
V6_LOOP = "0000000000000000FFFF00000100007F"


def row(local: str, remote: str, state: str) -> str:
    return f"   0: {local} {remote} {state} 0 0 0\n"


def test_identity_packet_points_the_phone_at_the_tunnel():
    text = identity_packet(LAPTOP_ID, "Test Laptop", now_ms=1)
    assert text.endswith("\n")
    body = json.loads(text)["body"]
    assert body["deviceId"] == LAPTOP_ID and body["tcpPort"] == PHONE_PORT
    assert json.loads(text)["type"] == "kdeconnect.identity"


def test_poke_command_quotes_odd_names():
    packet = identity_packet(LAPTOP_ID, "it's $(rm -rf) `x`", now_ms=1)
    words = shlex.split(poke_command(packet))
    assert words[:3] == ["printf", "%s", packet]
    assert f"{KDECONNECT_PORT};" in words


def test_cable_link_detection():
    port = f"{KDECONNECT_PORT:04X}"
    tunnel = row(f"{V6_LOOP}:{port}", f"{V6_LOOP}:9C40", "01")
    listening = row(f"{'0' * 32}:{port}", f"{'0' * 32}:0000", "0A")
    wifi = row(
        f"0000000000000000FFFF00000A00A8C0:{port}", "0000000000000000FFFF00000B00A8C0:9C40", "01"
    )
    other_port = row("0100007F:1F90", "0100007F:9C40", "01")
    assert cable_link_active([HEADER + listening + tunnel])
    assert cable_link_active([HEADER + row(f"0100007F:{port}", "0100007F:9C40", "01")])
    assert not cable_link_active([HEADER + listening + wifi, HEADER + other_port])
    assert not cable_link_active([HEADER + row(f"{V6_LOOP}:{port}", f"{V6_LOOP}:9C40", "06")])


ADB_TWO = (
    "List of devices attached\n"
    "USBSERIAL01            device usb:3-7 product:p model:Phone_A transport_id:1\n"
    "192.0.2.5:5555         device product:p model:Phone_B transport_id:2\n"
    "USBSERIAL02            unauthorized usb:3-8 transport_id:3\n\n"
)


def make_link(backend, runner, identity=(LAPTOP_ID, "Test Laptop")):
    backend.self_identity = lambda done: done(*identity)
    link = UsbLink(backend, runner, adb="/fake/bin/adb", poll_ms=60_000)
    return link


def test_does_nothing_while_every_paired_phone_is_reachable(qtbot, backend):
    backend.unpair(TABLET_ID)  # the fake tablet is paired but offline
    runner = FakeRunner()
    link = make_link(backend, runner)
    link.start()
    link.check()
    qtbot.wait(20)
    assert [c[1] for c in runner.calls] == [("forward", "--list")]  # only the start-up cleanup


def test_offers_kdeconnect_to_usb_phones_only(qtbot, backend):
    backend.set_reachable(PHONE_ID, False)
    runner = FakeRunner()
    runner.responses = {
        ("devices", "-l"): (0, ADB_TWO, ""),
        ("-s", "USBSERIAL01", "reverse"): (0, "", ""),
        ("-s", "USBSERIAL01", "shell"): (0, "", ""),
    }
    link = make_link(backend, runner)
    link.check()
    qtbot.waitUntil(lambda: len(runner.calls) == 3)
    qtbot.wait(20)
    args = [c[1] for c in runner.calls]
    assert args[0] == ("devices", "-l")
    assert args[1] == (
        "-s",
        "USBSERIAL01",
        "reverse",
        f"tcp:{PHONE_PORT}",
        f"tcp:{KDECONNECT_PORT}",
    )
    assert args[2][:3] == ("-s", "USBSERIAL01", "shell")
    assert LAPTOP_ID in args[2][3]
    assert len(runner.calls) == 3  # Wi-Fi and unauthorized devices were left alone
    link.check()  # and it can go again on the next tick
    qtbot.waitUntil(lambda: len(runner.calls) == 6)


def test_no_poke_when_reverse_fails(qtbot, backend):
    backend.set_reachable(PHONE_ID, False)
    runner = FakeRunner()
    runner.responses = {("devices", "-l"): (0, ADB_TWO, "")}  # reverse -> error
    link = make_link(backend, runner)
    link.check()
    qtbot.waitUntil(lambda: len(runner.calls) == 2)
    qtbot.wait(20)
    assert len(runner.calls) == 2
    assert not link._busy


def test_without_our_identity_adb_is_never_run(qtbot, backend):
    backend.set_reachable(PHONE_ID, False)
    runner = FakeRunner()
    link = make_link(backend, runner, identity=("", ""))
    link.check()
    qtbot.wait(20)
    assert runner.calls == [] and not link._busy


def test_only_paired_phones_out_of_reach_count(backend):
    link = make_link(backend, FakeRunner())
    assert link.needed()  # the fake tablet: paired, offline
    backend.unpair(TABLET_ID)
    assert not link.needed()
    backend.set_reachable(PHONE_ID, False)
    assert link.needed()


def listen(port: int, uid: str) -> str:
    return f"   1: 00000000:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000 {uid} 0 1\n"


PHONE_TABLES = (
    "package:org.kde.kdeconnect_tp uid:10123\n"
    + HEADER
    + listen(1716, "10123")  # KDE Connect's main port: not storage
    + listen(1739, "10123")  # the sftp server
    + listen(1764, "2000")  # our adb reverse (shell uid)
    + listen(1745, "10999")  # some other app
    + row("00000000:06CC", "0100007F:9C40", "01")  # an established one
)


def test_parse_sftp_ports():
    assert parse_sftp_ports(PHONE_TABLES) == [1739]
    assert parse_sftp_ports(HEADER + listen(1739, "10123")) == []  # uid unknown


def storage_runner():
    runner = FakeRunner()
    runner.responses = {
        ("devices", "-l"): (0, ADB_TWO, ""),
        ("-s", "USBSERIAL01", "shell"): (0, PHONE_TABLES, ""),
        ("-s", "USBSERIAL01", "forward"): (0, "", ""),
    }
    return runner


def test_forward_storage_forwards_the_sftp_port_once(qtbot, backend, monkeypatch):
    import mooring.services.usb_link as usb_link

    runner = storage_runner()
    link = make_link(backend, runner)
    results = []
    link.forward_storage(PHONE_ID, results.append)
    qtbot.waitUntil(lambda: results == [True])
    forwards = [c[1] for c in runner.calls if "forward" in c[1]]
    assert forwards == [("-s", "USBSERIAL01", "forward", "tcp:1739", "tcp:1739")]
    link.forward_storage(PHONE_ID, results.append)  # already forwarded: nothing new
    qtbot.waitUntil(lambda: len(results) == 2)
    assert results == [True, False]

    # once the cable link is gone, the forward is removed again
    monkeypatch.setattr(usb_link, "cable_link_active", lambda tables=None: False)
    link.check()
    qtbot.waitUntil(lambda: any("--remove" in c[1] for c in runner.calls))
    assert ("-s", "USBSERIAL01", "forward", "--remove", "tcp:1739") in [c[1] for c in runner.calls]


def test_forward_storage_without_a_server_reports_no_change(qtbot, backend):
    runner = storage_runner()
    runner.responses[("-s", "USBSERIAL01", "shell")] = (
        0,
        "package:org.kde.kdeconnect_tp uid:10123\n",
        "",
    )
    link = make_link(backend, runner)
    results = []
    link.forward_storage(PHONE_ID, results.append)
    qtbot.waitUntil(lambda: results == [False])


def test_start_hands_forward_storage_to_a_backend_that_wants_it(backend):
    backend.forward_storage = None
    link = make_link(backend, FakeRunner())
    link.start()
    assert backend.forward_storage == link.forward_storage


def test_stale_storage_forwards_are_removed_at_start(qtbot, backend):
    runner = FakeRunner()
    runner.responses = {
        ("forward", "--list"): (
            0,
            "USBSERIAL01 tcp:1739 tcp:1739\nUSBSERIAL01 tcp:8080 tcp:8080\n"
            "USBSERIAL01 tcp:1740 localabstract:scrcpy_1234\n",
            "",
        ),
        ("-s", "USBSERIAL01", "forward", "--remove"): (0, "", ""),
    }
    link = make_link(backend, runner)
    link.start()
    qtbot.waitUntil(lambda: any("--remove" in c[1] for c in runner.calls))
    removed = [c[1] for c in runner.calls if "--remove" in c[1]]
    assert removed == [("-s", "USBSERIAL01", "forward", "--remove", "tcp:1739")]


def test_parse_storage_forwards_leaves_other_forwards_alone():
    text = "S1 tcp:1739 tcp:1739\nS1 tcp:27183 localabstract:scrcpy_x\nS2 tcp:1745 tcp:1746\n"
    assert parse_storage_forwards(text) == [("S1", 1739)]


def test_storage_forward_waits_for_the_startup_cleanup(qtbot, backend):
    runner = storage_runner()
    runner.responses[("forward", "--list")] = (0, "USBSERIAL01 tcp:1739 tcp:1739\n", "")
    link = make_link(backend, runner)
    link.start()  # cleanup in flight
    results = []
    link.forward_storage(PHONE_ID, results.append)
    qtbot.waitUntil(lambda: results == [True])
    forwards = [c[1][2:] for c in runner.calls if len(c[1]) > 2 and c[1][2] == "forward"]
    # the stale one goes first, then ours is added (and stays)
    assert forwards == [("forward", "--remove", "tcp:1739"), ("forward", "tcp:1739", "tcp:1739")]
