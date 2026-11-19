"""Phone number -> contact name, from the vCards KDE Connect's contacts plugin syncs.

kdeconnectd writes them to ``~/.local/share/kpeoplevcard/kdeconnect-<deviceId>/``.
The phone app needs the Contacts permission for that folder to fill up.
Nothing here is ever logged except counts.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import quopri
import re
from pathlib import Path

log = logging.getLogger(__name__)

#: Numbers match when their last N digits agree (ignores +46 vs 0 prefixes etc.).
MATCH_DIGITS = 9


def vcard_dir(device_id: str) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "kpeoplevcard" / f"kdeconnect-{device_id}"


def number_key(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return digits[-MATCH_DIGITS:]


def _decode(params: list[str], value: str) -> str:
    """Apply vCard 2.1 ENCODING/CHARSET params (quoted-printable, base64)."""
    upper = [p.upper() for p in params]
    charset = next((p.split("=", 1)[1] for p in params if p.upper().startswith("CHARSET=")), "")
    raw: bytes | None = None
    if "ENCODING=QUOTED-PRINTABLE" in upper or "QUOTED-PRINTABLE" in upper:
        raw = quopri.decodestring(value.encode("ascii", "replace"))
    elif any(p in upper for p in ("ENCODING=B", "ENCODING=BASE64", "BASE64")):
        try:
            raw = base64.b64decode(value)
        except (binascii.Error, ValueError):
            return value
    if raw is None:
        return value
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:  # unknown charset name
        return raw.decode("utf-8", errors="replace")


def _unescape(value: str) -> str:
    return value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()


def _logical_lines(text: str) -> list[str]:
    """Unfold RFC folding (leading space) and quoted-printable soft breaks (trailing '=')."""
    lines: list[str] = []
    for line in text.splitlines():
        if lines and line[:1] in (" ", "\t"):
            lines[-1] += line[1:]
        elif lines and lines[-1].endswith("=") and "QUOTED-PRINTABLE" in lines[-1].upper():
            lines[-1] = lines[-1][:-1] + line
        else:
            lines.append(line)
    return lines


def parse_vcard(text: str) -> tuple[str | None, list[str]]:
    """Return (display name, phone numbers). FN preferred, else 'Given Family' from N."""
    fn: str | None = None
    structured: str | None = None
    numbers: list[str] = []
    for line in _logical_lines(text):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        prop, *params = key.split(";")
        prop = prop.upper()
        if prop == "FN":
            fn = _unescape(_decode(params, value)) or fn
        elif prop == "N":
            parts = [p.strip() for p in _decode(params, value).split(";")]
            family, given = [*parts, "", ""][:2]
            structured = " ".join(x for x in (given, family) if x) or structured
        elif prop == "TEL" and value.strip():
            numbers.append(value.strip().removeprefix("tel:"))
    return fn or structured, numbers


class ContactBook:
    """Lazily (re)loads when the vCard folder changes."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._stamp: tuple[float, int] | None = None
        self._names: dict[str, str] = {}

    @classmethod
    def for_device(cls, device_id: str) -> ContactBook:
        return cls(vcard_dir(device_id))

    def _current_stamp(self) -> tuple[float, int] | None:
        try:
            files = list(self._dir.glob("*.vcf"))
        except OSError:
            return None
        if not files:
            return (0.0, 0)
        return (max(f.stat().st_mtime for f in files), len(files))

    def _maybe_reload(self) -> None:
        stamp = self._current_stamp()
        if stamp == self._stamp:
            return
        self._stamp = stamp
        names: dict[str, str] = {}
        for path in self._dir.glob("*.vcf") if stamp else ():
            try:
                name, numbers = parse_vcard(path.read_text("utf-8", errors="replace"))
            except OSError:
                continue
            if name:
                for n in numbers:
                    if key := number_key(n):
                        names.setdefault(key, name)
        self._names = names
        log.info("contacts: %d numbers from %d vCards", len(names), stamp[1] if stamp else 0)

    def name_for(self, number: str) -> str | None:
        self._maybe_reload()
        key = number_key(number)
        return self._names.get(key) if key else None

    def display_name(self, addresses: tuple[str, ...]) -> str:
        return ", ".join(self.name_for(a) or a for a in addresses) or "Unknown"
