"""Decoding with synthetic payloads shaped exactly like kdeconnectd's (see docs)."""

import base64
from datetime import datetime

from mooring.backend.sms_decode import SIGNATURE, decode_message, encode_addresses


def struct(
    body="Hello",
    addresses=("+46700000001",),
    date_ms=1_763_470_000_000,
    kind=1,
    read=1,
    thread=7,
    uid=42,
    attachments=(),
):
    return (
        1,
        body,
        [(a,) for a in addresses],
        date_ms,
        kind,
        read,
        thread,
        uid,
        1,
        list(attachments),
    )


def test_decodes_variant():
    m = decode_message((SIGNATURE, struct()))
    assert m is not None
    assert (m.id, m.thread_id, m.body, m.addresses) == (42, 7, "Hello", ("+46700000001",))
    assert m.timestamp == datetime.fromtimestamp(1_763_470_000)
    assert not m.outgoing and m.read and m.attachments == ()


def test_bare_struct_and_message_types():
    assert decode_message(struct(kind=1)).outgoing is False  # inbox
    for kind in (2, 4, 5, 6):  # sent, outbox, failed, queued
        assert decode_message(struct(kind=kind)).outgoing is True
    assert decode_message(struct(read=0)).read is False


def test_group_addresses():
    m = decode_message(struct(addresses=("+46700000001", "+46700000002")))
    assert m.addresses == ("+46700000001", "+46700000002")


def test_attachments():
    png = b"\x89PNG fake"
    m = decode_message(
        struct(
            body="",
            attachments=[
                (3, "image/jpeg", base64.b64encode(png).decode(), "uid-3"),
                (4, "text/plain", "", "uid-4"),
                (5, "image/png", "!!not base64!!", "uid-5"),
            ],
        )
    )
    a, b, c = m.attachments
    assert (a.part_id, a.mime_type, a.unique_id, a.thumbnail, a.is_image) == (
        3,
        "image/jpeg",
        "uid-3",
        png,
        True,
    )
    assert not b.is_image
    assert c.thumbnail == b""  # bad base64 tolerated


def test_malformed_and_wrong_signature_return_none(caplog):
    assert decode_message(("(is)", (1, "x"))) is None
    assert decode_message((1, 2, 3)) is None
    assert decode_message(None) is None
    assert "Hello" not in caplog.text  # never logs payloads


def test_encode_addresses():
    assert encode_addresses(("+1", "+2")) == [("(s)", ("+1",)), ("(s)", ("+2",))]
