import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings

from mooring.services.settings import SettingsStore
from mooring.theme.catppuccin import Theme
from tests.fakes.fake_backend import PHONE_ID, FakeBackend


@pytest.fixture
def backend(qapp):
    return FakeBackend(auto_reply_ms=None)


@pytest.fixture
def settings(qapp, tmp_path):
    store = SettingsStore(QSettings(str(tmp_path / "mooring.conf"), QSettings.Format.IniFormat))
    store.set_active_device(PHONE_ID)
    return store


@pytest.fixture
def theme(qapp):
    return Theme("mocha")
