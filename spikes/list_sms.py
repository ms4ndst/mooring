"""Phase 0 spike: prove we can reach kdeconnectd from Python and decode SMS payloads.

PRIVACY: prints IDs, counts, booleans and D-Bus *types* only. Never message bodies,
phone numbers, contact names or device names. Keep it that way.

    .venv/bin/python spikes/list_sms.py            # first reachable paired device
    .venv/bin/python spikes/list_sms.py --sync     # also ask the phone to send threads

Uses jeepney rather than QtDBus because PyQt6's QDBusArgument cannot be read
(no demarshalling), so the conversation structs are undecodable there.
"""

from __future__ import annotations

import argparse
import sys
import time

from jeepney import DBusAddress, MatchRule, Properties, message_bus, new_method_call
from jeepney.io.blocking import DBusConnection, Proxy, open_dbus_connection
from jeepney.wrappers import DBusErrorResponse, unwrap_msg

SERVICE = "org.kde.kdeconnect"
DAEMON = DBusAddress("/modules/kdeconnect", SERVICE, "org.kde.kdeconnect.daemon")
DEVICE_IFACE = "org.kde.kdeconnect.device"
CONV_IFACE = "org.kde.kdeconnect.device.conversations"
SMS_IFACE = "org.kde.kdeconnect.device.sms"
BATTERY_IFACE = "org.kde.kdeconnect.device.battery"


def short(device_id: str) -> str:
    return device_id[:6] + "…"


def call(conn: DBusConnection, addr: DBusAddress, method: str, sig: str = "", args: tuple = ()):
    # unwrap_msg raises DBusErrorResponse; plain .body would silently return the error
    return unwrap_msg(conn.send_and_get_reply(new_method_call(addr, method, sig, args)))


def props(conn: DBusConnection, addr: DBusAddress) -> dict:
    body = unwrap_msg(conn.send_and_get_reply(Properties(addr).get_all()))
    return {k: v for k, (_sig, v) in body[0].items()}


def describe(value, depth: int = 0) -> str:
    """D-Bus-ish type sketch of a decoded value. Never prints the value itself."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) and depth == 0:
        # jeepney variant: (signature, value)
        return f"v[{value[0]}]"
    if isinstance(value, tuple):
        return "(" + ",".join(describe(v, depth + 1) for v in value) + ")"
    if isinstance(value, list):
        return f"a[{len(value)}]"
    return type(value).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sync", action="store_true", help="call conversations.requestAllConversationThreads"
    )
    ap.add_argument("--wait", type=float, default=8.0, help="seconds to wait for signals")
    ap.add_argument("--thread", type=int, help="thread id to fetch (requestConversation 0..20)")
    opts = ap.parse_args()

    conn = open_dbus_connection(bus="SESSION")

    paired = call(conn, DAEMON, "devices", "bb", (False, True))[0]
    reachable = call(conn, DAEMON, "devices", "bb", (True, True))[0]
    print(f"devices: {len(paired)} paired, {len(reachable)} reachable+paired")
    if not reachable:
        print("no reachable paired device; nothing more to do")
        return 1
    dev_id = reachable[0]
    dev_path = f"/modules/kdeconnect/devices/{dev_id}"
    print(f"using device {short(dev_id)}")

    # Device properties: names and booleans only
    dev = props(conn, DBusAddress(dev_path, SERVICE, DEVICE_IFACE))
    print("device properties:", sorted(dev))
    for key, value in sorted(dev.items()):
        if isinstance(value, bool | int) or key == "type":  # skip names / free text
            print(f"  {key} = {value!r}")

    # Battery
    try:
        bat = props(conn, DBusAddress(f"{dev_path}/battery", SERVICE, BATTERY_IFACE))
        print(f"battery: charge={bat.get('charge')} isCharging={bat.get('isCharging')}")
    except DBusErrorResponse as e:
        print("battery: unavailable:", e.name)

    # Subscribe to conversation signals before asking for anything
    bus = Proxy(message_bus, conn)
    rule = MatchRule(type="signal", interface=CONV_IFACE, path=dev_path)
    bus.AddMatch(rule)

    conv_addr = DBusAddress(dev_path, SERVICE, CONV_IFACE)
    with conn.filter(rule, bufsize=10_000) as queue:
        if opts.sync:
            # Lives on the conversations interface (device path), not on /sms.
            call(conn, conv_addr, "requestAllConversationThreads")
            print("requested all conversation threads from the phone")

        active = call(conn, conv_addr, "activeConversations")[0]
        print(f"activeConversations: {len(active)} entries")
        if active:
            print("  first entry type sketch:", describe(active[0]))

        if opts.thread is not None:
            call(conn, conv_addr, "requestConversation", "xii", (opts.thread, 0, 20))
            print(f"requested messages 0..20 of thread {opts.thread}")

        counts: dict[str, int] = {}
        loaded: list[tuple] = []
        deadline = time.monotonic() + opts.wait
        while time.monotonic() < deadline:
            try:
                msg = conn.recv_until_filtered(queue, timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            member = msg.header.fields.get(3)  # HeaderFields.member
            counts[member] = counts.get(member, 0) + 1
            if member == "conversationLoaded":
                loaded.append(msg.body)
            if member in ("conversationCreated", "conversationUpdated") and counts[member] == 1:
                sig = msg.header.fields.get(8)  # HeaderFields.signature
                print(f"  {member} sig={sig} body sketch:", describe(msg.body[0]))

        print("signals received:", counts or "none")
        for body in loaded:
            print(f"  conversationLoaded: thread={body[0]} messageCount={body[1]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
