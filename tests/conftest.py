import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings

from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from tests.fakes.fake_backend import PHONE_ID, FakeBackend


@pytest.fixture
def backend(qapp, tmp_path):
    return FakeBackend(auto_reply_ms=None, storage_parent=str(tmp_path))


@pytest.fixture
def settings(qapp, tmp_path):
    store = SettingsStore(QSettings(str(tmp_path / "mooring.conf"), QSettings.Format.IniFormat))
    store.set_active_device(PHONE_ID)
    return store


@pytest.fixture
def theme(qapp):
    return Theme("mocha")


@pytest.fixture(autouse=True)
def _isolate_user_data(tmp_path, monkeypatch):
    """Tests must never read real contacts, or write to the real cache or config."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch):
    """No test may run the real adb/scrcpy (it would talk to a real phone)."""
    import mooring.backend.mirror as mirror
    from tests.fakes.fake_runner import FakeRunner

    runner = FakeRunner()
    monkeypatch.setattr(
        mirror.shutil,
        "which",
        lambda name: f"/fake/bin/{name}" if name in ("adb", "scrcpy") else None,
    )
    monkeypatch.setattr(mirror, "find_server", lambda: "/fake/share/scrcpy/scrcpy-server")
    original_init = mirror.MirrorBackend.__init__

    def init(self, runner_arg=None, parent=None):
        original_init(self, runner_arg or runner, parent)

    monkeypatch.setattr(mirror.MirrorBackend, "__init__", init)
    return runner


@pytest.fixture(autouse=True)
def _no_real_cable_probe(monkeypatch):
    """Tests must not depend on whether this machine has a phone tunnelled over USB."""
    import mooring.services.usb_link as usb_link

    monkeypatch.setattr(usb_link, "cable_link_active", lambda tables=None: False)
