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
    """Tests must never read real synced contacts or write to the real cache."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
