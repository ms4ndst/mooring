"""Decode KDE Connect's ConversationMessage D-Bus struct into ``Message``.

Wire format (verified live against kdeconnectd 26.08, see docs/dbus-introspection.txt),
always wrapped in a variant: ``(isa(s)xiixixa(xsss))``

    [0] i  eventField   bit flags (1 = text message, 2 = multi-target)
    [1] s  body
    [2] a(s) addresses
    [3] x  date         milliseconds since epoch
    [4] i  type         Android Telephony: 1 inbox, 2 sent, 3 draft, 4 outbox,
                        5 failed, 6 queued
    [5] i  read         0/1
    [6] x  threadID
    [7] i  uID          message id
    [8] x  subID        SIM subscription
    [9] a(xsss) attachments: (partID, mimeType, base64 thumbnail, uniqueIdentifier)

Never log the decoded values. On malformed input, log the *shape* only.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime
from typing import Any

from mooring.backend.base import Attachment, Message

log = logging.getLogger(__name__)

SIGNATURE = "(isa(s)xiixixa(xsss))"
TYPE_INBOX = 1


def _attachment(raw: Any) -> Attachment | None:
    try:
        part_id, mime, b64, unique_id = raw
        try:
            thumb = base64.b64decode(b64) if b64 else b""
        except (binascii.Error, ValueError):
            thumb = b""
        return Attachment(int(part_id), str(mime), str(unique_id), thumb)
    except (TypeError, ValueError):
        return None


def decode_message(value: Any) -> Message | None:
    """Accepts the variant ``(signature, struct)`` or the bare struct."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        if value[0] != SIGNATURE:
            log.warning("unexpected ConversationMessage signature %s", value[0])
            return None
        value = value[1]
    try:
        (_event, body, addresses, date_ms, kind, read, thread, uid, _sub, attachments) = value
        return Message(
            id=int(uid),
            thread_id=int(thread),
            addresses=tuple(str(a[0]) for a in addresses),
            body=str(body),
            timestamp=datetime.fromtimestamp(int(date_ms) / 1000),
            outgoing=int(kind) != TYPE_INBOX,
            read=bool(read),
            attachments=tuple(a for a in map(_attachment, attachments) if a is not None),
        )
    except (TypeError, ValueError, IndexError, OverflowError, OSError):
        shape = f"{type(value).__name__}[{len(value) if hasattr(value, '__len__') else '?'}]"
        log.warning("could not decode ConversationMessage (%s)", shape)
        return None


def encode_addresses(addresses: tuple[str, ...]) -> list[tuple[str, tuple[str]]]:
    """Addresses for ``sendWithoutConversation(av, ...)``: each a ConversationAddress ``(s)``."""
    return [("(s)", (a,)) for a in addresses]
