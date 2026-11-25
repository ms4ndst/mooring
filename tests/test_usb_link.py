"""KDE Connect over the USB cable, against fake adb. Synthetic IDs only."""

import json
import shlex

from mooring.services.usb_link import (
    KDECONNECT_PORT,
    PHONE_PORT,
    UsbLink,
    cable_link_active,  # the real one: conftest replaces the module attribute
    identity_packet,
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
    assert runner.calls == []


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
