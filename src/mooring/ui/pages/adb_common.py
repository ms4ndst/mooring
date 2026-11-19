"""Shared by the Apps and Mirror pages: setup help and which ADB device is "the phone"."""

from __future__ import annotations

from mooring.backend.base import PhoneBackend
from mooring.backend.mirror import AdbDevice, MirrorBackend, pick_adb_device
from mooring.services.settings import SettingsStore

SETUP_TOOLS = (
    "This needs adb and scrcpy:\n\nsudo dnf install android-tools scrcpy\n\nThen press Refresh."
)
SETUP_DEVICE = (
    "No phone found over ADB.\n\n"
    "1. On the phone: Settings → About phone → tap “Build number” 7 times.\n"
    "2. Settings → System → Developer options → turn on USB debugging\n"
    "    (or Wireless debugging, then use “Pair over Wi-Fi” on the Apps page).\n"
    "3. Connect the USB cable and allow this computer on the phone.\n\n"
    "Then press Refresh."
)
SETUP_UNAUTHORIZED = (
    "The phone hasn't allowed this computer yet.\n\n"
    "Unlock the phone and accept “Allow USB debugging?”, then press Refresh."
)


def chosen_device(
    mirror: MirrorBackend, backend: PhoneBackend, settings: SettingsStore
) -> AdbDevice | None:
    """The ADB device for the active KDE Connect phone (saved choice, model, or only one)."""
    kde_id = settings.active_device
    kde = backend.device(kde_id)
    return pick_adb_device(
        mirror.devices(), kde.name if kde else "", settings.adb_serial_for(kde_id)
    )


def setup_help(mirror: MirrorBackend) -> str | None:
    """Why there's no usable phone, or None if there is one."""
    if not mirror.tools.ready:
        return SETUP_TOOLS
    devices = mirror.devices()
    if any(d.usable for d in devices):
        return None
    if any(d.state == "unauthorized" for d in devices):
        return SETUP_UNAUTHORIZED
    return SETUP_DEVICE
