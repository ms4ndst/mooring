"""Finding PyAV, the optional decoder for the embedded mirror.

PyAV isn't packaged for Fedora, so users install it with ``pip install --user av``.
Fedora's RPM launchers run Python with ``-sP`` though, and ``-s`` hides the user
site-packages directory, so the RPM build of Mooring couldn't see that install.

When ``av`` isn't importable normally, look for it in the user site-packages and, if
it's there, append that directory to ``sys.path`` (last, so system packages still
win for everything else). Nothing changes when av is installed system-wide or in a venv.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import site
import sys
from pathlib import Path
from types import ModuleType

log = logging.getLogger(__name__)


def user_site_with_av(user_site: str | None = None) -> str | None:
    """The user site-packages directory if it contains PyAV, else None."""
    path = user_site if user_site is not None else site.getusersitepackages()
    if path and (Path(path) / "av" / "__init__.py").is_file():
        return path
    return None


def pyav_available() -> bool:
    if importlib.util.find_spec("av") is not None:
        return True
    path = user_site_with_av()
    if path is None:
        return False
    if path not in sys.path:
        sys.path.append(path)  # last: never shadows system packages
        importlib.invalidate_caches()
        log.info("using PyAV from the user site-packages")
    return importlib.util.find_spec("av") is not None


def import_av() -> ModuleType:
    """Import PyAV (raising ImportError if it isn't installed anywhere usable)."""
    if not pyav_available():
        raise ImportError("PyAV is not installed (pip install --user av)")
    return importlib.import_module("av")
