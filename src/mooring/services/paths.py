"""Private on-disk locations. Caches hold copies of personal data, so they're 0700."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def cache_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "mooring"


def private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) and force mode 0700 on it and on ``cache_root``."""
    path.mkdir(parents=True, exist_ok=True)
    for p in {cache_root(), path}:
        if p.exists():
            p.chmod(0o700)
    return path


def clear_cache() -> int:
    """Delete everything Mooring cached. Returns the number of files removed."""
    root = cache_root()
    if not root.exists():
        return 0
    count = sum(1 for p in root.rglob("*") if p.is_file())
    shutil.rmtree(root)
    return count


def cache_size() -> int:
    root = cache_root()
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
