"""Desktop integration for user installs: autostart entry and a launcher entry.

The RPM installs the launcher, icon and metainfo system-wide. Running from a venv,
``mooring --install-desktop-entry`` puts equivalents in ~/.local/share so the desktop
(and the portal, which otherwise warns "App info not found") know the app ID.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from mooring.resources.icons import APP_ID, svg_source

ENTRY = """[Desktop Entry]
Type=Application
Name=Mooring
GenericName=Phone Hub
Comment=Messages, notifications, photos, files and screen mirroring for your Android phone
Exec={exec}
Icon={app_id}
Terminal=false
Categories=Network;InstantMessaging;
StartupNotify=true
SingleMainWindow=true
"""

AUTOSTART_EXTRA = "X-GNOME-Autostart-enabled=true\nX-KDE-autostart-after=panel\n"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def autostart_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / f"{APP_ID}.desktop"


def applications_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "applications" / f"{APP_ID}.desktop"


def icon_path() -> Path:
    base = _xdg("XDG_DATA_HOME", ".local/share")
    return base / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def launch_command() -> str:
    """How to start this Mooring: its console script next to the running Python
    (/usr/bin/mooring for the RPM, .venv/bin/mooring in a venv), else ``python -m``."""
    script = Path(sys.executable).parent / "mooring"
    if script.is_file() and os.access(script, os.X_OK):
        return shlex.quote(str(script))
    return f"{shlex.quote(sys.executable)} -m mooring"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def autostart_enabled() -> bool:
    return autostart_path().is_file()


def set_autostart(enabled: bool) -> None:
    """Start Mooring hidden in the tray at login (or stop doing so)."""
    path = autostart_path()
    if enabled:
        entry = ENTRY.format(exec=f"{launch_command()} --hidden", app_id=APP_ID)
        _write(path, entry + AUTOSTART_EXTRA)
    elif path.exists():
        path.unlink()


def install_desktop_entry() -> list[Path]:
    """Launcher + icon for the current user. Returns the files written."""
    _write(applications_path(), ENTRY.format(exec=launch_command(), app_id=APP_ID))
    _write(icon_path(), svg_source("app"))
    return [applications_path(), icon_path()]
