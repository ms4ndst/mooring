"""PyAV lookup, incl. a `pip install --user av` hidden by the RPM launcher's `python3 -s`."""

import importlib.machinery
import sys

from mooring.backend import pyav


def fake_user_site(tmp_path):
    pkg = tmp_path / "av"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__version__ = 'fake'\n")
    return str(tmp_path)


def test_user_site_with_av(tmp_path):
    assert pyav.user_site_with_av(str(tmp_path)) is None
    site_dir = fake_user_site(tmp_path)
    assert pyav.user_site_with_av(site_dir) == site_dir


def test_falls_back_to_user_site_when_hidden(tmp_path, monkeypatch):
    site_dir = fake_user_site(tmp_path)
    monkeypatch.setattr(pyav.site, "getusersitepackages", lambda: site_dir)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != site_dir])

    def find_spec(name, *args):
        # simulate `python3 -s`: av only exists in the (hidden) user site
        if name != "av":
            return importlib.machinery.PathFinder.find_spec(name)
        return importlib.machinery.PathFinder.find_spec(
            name, [p for p in sys.path if p == site_dir]
        )

    monkeypatch.setattr(pyav.importlib.util, "find_spec", find_spec)
    assert pyav.pyav_available()
    assert sys.path[-1] == site_dir  # appended last: never shadows system packages


def test_not_installed_anywhere(tmp_path, monkeypatch):
    monkeypatch.setattr(pyav.site, "getusersitepackages", lambda: str(tmp_path))
    monkeypatch.setattr(pyav.importlib.util, "find_spec", lambda name, *a: None)
    assert not pyav.pyav_available()
