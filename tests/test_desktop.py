"""Desktop integration: autostart, launcher entry, icons, metainfo. All in tmp XDG dirs."""

import shutil
import subprocess
from pathlib import Path

import pytest

from mooring.app import parse_args
from mooring.resources.icons import APP_ID, app_icon, svg_source, tinted_icon
from mooring.services import desktop

DATA = Path(__file__).resolve().parents[1] / "data"


def test_autostart_on_and_off(tmp_path):
    assert not desktop.autostart_enabled()
    desktop.set_autostart(True)
    path = desktop.autostart_path()
    assert path.is_relative_to(tmp_path)  # never the real ~/.config
    text = path.read_text()
    assert "--hidden" in text and f"Icon={APP_ID}" in text
    assert desktop.autostart_enabled()
    desktop.set_autostart(False)
    assert not path.exists() and not desktop.autostart_enabled()


def test_install_desktop_entry(tmp_path):
    written = desktop.install_desktop_entry()
    assert all(p.is_relative_to(tmp_path) for p in written)
    entry, icon = written
    assert entry.name == f"{APP_ID}.desktop" and "Exec=" in entry.read_text()
    assert icon.read_text().lstrip().startswith("<svg")


@pytest.mark.skipif(not shutil.which("desktop-file-validate"), reason="desktop-file-utils")
def test_generated_and_shipped_desktop_files_validate(tmp_path):
    desktop.set_autostart(True)
    files = [*desktop.install_desktop_entry()[:1], desktop.autostart_path()]
    files.append(DATA / f"{APP_ID}.desktop")
    for f in files:
        result = subprocess.run(["desktop-file-validate", str(f)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hint" not in result.stdout


@pytest.mark.skipif(not shutil.which("appstreamcli"), reason="appstream")
def test_metainfo_validates():
    result = subprocess.run(
        ["appstreamcli", "validate", "--no-net", str(DATA / f"{APP_ID}.metainfo.xml")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_launch_command_points_at_this_install():
    cmd = desktop.launch_command()
    assert cmd.endswith("mooring") or cmd.endswith("-m mooring")


def test_icons_render(qapp):
    for name in ("messages", "photos", "notifications", "files", "apps", "mirror", "settings"):
        assert "currentColor" in svg_source(name)
        icon = tinted_icon(name, "#cdd6f4", "#ffffff")
        assert not icon.pixmap(20, 20).isNull()
    assert not app_icon().pixmap(64, 64).isNull()


def test_hidden_flag():
    args, _ = parse_args(["--hidden", "--fake"])
    assert args.hidden and args.fake
