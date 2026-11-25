"""The interface between the UI and whatever talks to the phone.

The UI only ever sees these dataclasses and the ``PhoneBackend`` signals. D-Bus
variants, scrcpy output and so on are decoded inside the concrete backends.

Calls that hit the phone are asynchronous: a ``request_*`` method returns at
once and its result arrives later through a signal. Cheap reads of state the
backend already holds (``devices()``, ``conversations()``) are synchronous.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    type: str = "phone"
    reachable: bool = False
    trusted: bool = False
    battery: int | None = None
    charging: bool = False
    #: Active transports, e.g. ("Wi-Fi", "Bluetooth"). Empty when unknown/offline.
    links: tuple[str, ...] = ()
    #: KDE Connect's clipboard plugin on/off for this device (None = unknown).
    clipboard_sync: bool | None = None


@dataclass(frozen=True, slots=True)
class Attachment:
    """An MMS part. ``thumbnail`` is a small preview; the full file is fetched on demand."""

    part_id: int
    mime_type: str
    unique_id: str
    thumbnail: bytes = b""

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    thread_id: int
    addresses: tuple[str, ...]
    body: str
    timestamp: datetime
    outgoing: bool
    read: bool = True
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True, slots=True)
class Conversation:
    thread_id: int
    addresses: tuple[str, ...]
    display_name: str
    last_message: Message
    unread: bool = False


@dataclass(frozen=True, slots=True)
class Notification:
    id: str
    app_name: str
    title: str
    text: str
    timestamp: datetime
    dismissable: bool = True
    repliable: bool = False
    #: Local path of the app icon kdeconnectd downloaded ("" = none yet).
    icon_path: str = ""


class PhoneBackend(QObject):
    """Abstract backend. Subclasses implement every method below.

    This is a QObject rather than an ``abc.ABC`` because the two metaclasses
    don't mix; unimplemented methods raise ``NotImplementedError`` instead.
    """

    #: The set of known devices changed (added, removed, paired, unpaired).
    devicesChanged = pyqtSignal()
    #: One device's state changed (reachability, battery). Arg: device id.
    deviceChanged = pyqtSignal(str)
    #: The conversation list for a device changed. Arg: device id.
    conversationsChanged = pyqtSignal(str)
    #: Result of ``request_conversation``.
    #: Args: device id, thread id, start index, messages (oldest first).
    messagesLoaded = pyqtSignal(str, object, int, list)
    #: A single new message (incoming or sent). Args: device id, Message.
    messageAdded = pyqtSignal(str, object)
    #: Args: device id, Notification.
    notificationPosted = pyqtSignal(str, object)
    #: Args: device id, notification id.
    notificationRemoved = pyqtSignal(str, str)
    #: A requested attachment was downloaded. Args: device id, local file path.
    attachmentReady = pyqtSignal(str, str)
    #: Phone storage is mounted. Args: device id, local path of internal storage.
    storageReady = pyqtSignal(str, str)
    #: Mounting failed. Args: device id, short reason.
    storageFailed = pyqtSignal(str, str)
    #: Something failed that the user should hear about. Arg: short message.
    error = pyqtSignal(str)

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Connect to the daemon and emit ``devicesChanged`` once devices are known."""
        raise NotImplementedError

    def self_identity(self, done: Callable[[str, str], None]) -> None:
        """This computer's KDE Connect id and name; ``done("", "")`` if unknown."""
        done("", "")

    # --- devices -------------------------------------------------------------

    def devices(self) -> list[Device]:
        raise NotImplementedError

    def device(self, device_id: str) -> Device | None:
        return next((d for d in self.devices() if d.id == device_id), None)

    def request_pairing(self, device_id: str) -> None:
        raise NotImplementedError

    def unpair(self, device_id: str) -> None:
        raise NotImplementedError

    def ring(self, device_id: str) -> None:
        raise NotImplementedError

    # --- messages ------------------------------------------------------------

    def conversations(self, device_id: str) -> list[Conversation]:
        raise NotImplementedError

    def request_conversation(self, device_id: str, thread_id: int, start: int, end: int) -> None:
        """Load messages ``start``..``end`` of a thread, counted from the newest (0).

        Mirrors KDE Connect's ``requestConversation(threadId, start, end)``.
        Answered by ``messagesLoaded``.
        """
        raise NotImplementedError

    def send_message(
        self,
        device_id: str,
        addresses: tuple[str, ...],
        text: str,
        thread_id: int | None = None,
    ) -> None:
        raise NotImplementedError

    # --- notifications -------------------------------------------------------

    def notifications(self, device_id: str) -> list[Notification]:
        raise NotImplementedError

    def dismiss_notification(self, device_id: str, notification_id: str) -> None:
        raise NotImplementedError

    def reply_notification(self, device_id: str, notification_id: str, text: str) -> None:
        """Answer a chat notification (only when ``Notification.repliable``)."""
        raise NotImplementedError

    # --- sharing -------------------------------------------------------------

    def send_clipboard(self, device_id: str) -> None:
        raise NotImplementedError

    def share_url(self, device_id: str, url: str) -> None:
        raise NotImplementedError

    # --- attachments ---------------------------------------------------------

    def request_attachment(self, device_id: str, attachment: Attachment) -> None:
        """Download the full file; answered by ``attachmentReady``."""
        raise NotImplementedError

    # --- storage -------------------------------------------------------------

    def mount_storage(self, device_id: str) -> None:
        """Mount the phone's storage locally; answered by ``storageReady`` or ``error``."""
        raise NotImplementedError

    def storage_root(self, device_id: str) -> str | None:
        """Local path of the mounted internal storage, if mounted."""
        raise NotImplementedError

    # --- clipboard -----------------------------------------------------------

    def set_clipboard_sync(self, device_id: str, enabled: bool) -> None:
        """Turn KDE Connect's automatic clipboard sync for the device on/off."""
        raise NotImplementedError
