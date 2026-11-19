"""Photo scanning and thumbnails, on synthetic files in tmp dirs."""

import os
import stat
import time

from PIL import Image

from mooring.services.paths import cache_root, cache_size, clear_cache
from mooring.services.photos import scan_photos
from mooring.services.thumbnails import ThumbnailCache, make_thumbnail, thumb_dir, thumb_key


def touch_image(path, age_hours=0.0, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "#8aadf4").save(path, "JPEG")
    t = time.time() - age_hours * 3600
    os.utime(path, (t, t))
    return path


def test_scan_finds_newest_first_and_skips_noise(tmp_path):
    touch_image(tmp_path / "DCIM/Camera/old.jpg", age_hours=5)
    touch_image(tmp_path / "DCIM/Camera/new.JPG", age_hours=1)
    touch_image(tmp_path / "Pictures/Screenshots/shot.png", age_hours=2)
    touch_image(tmp_path / "DCIM/OpenCamera/other.jpg", age_hours=3)  # non-standard folder
    touch_image(tmp_path / "Pictures/.thumbnails/hidden.jpg")
    touch_image(tmp_path / "DCIM/Camera/.pending.jpg")
    (tmp_path / "DCIM/Camera/notes.txt").write_text("x")
    touch_image(tmp_path / "Download/elsewhere.jpg")  # not a photo folder

    names = [p.name for p in scan_photos(tmp_path)]
    assert names == ["new.JPG", "shot.png", "other.jpg", "old.jpg"]


def test_scan_limit_and_missing_folders(tmp_path):
    assert scan_photos(tmp_path) == []
    for i in range(5):
        touch_image(tmp_path / f"DCIM/Camera/{i}.jpg", age_hours=i)
    assert [p.name for p in scan_photos(tmp_path, limit=2)] == ["0.jpg", "1.jpg"]


def test_make_thumbnail_respects_size_and_permissions(tmp_path):
    src = touch_image(tmp_path / "big.jpg", size=(1200, 800))
    out = tmp_path / "t.jpg"
    assert make_thumbnail(str(src), out, size=256)
    with Image.open(out) as im:
        assert max(im.size) == 256
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_make_thumbnail_rejects_non_images(tmp_path):
    bad = tmp_path / "x.jpg"
    bad.write_bytes(b"nope")
    assert not make_thumbnail(str(bad), tmp_path / "t.jpg")


def test_thumbnail_cache_is_private_and_keyed_without_names(qtbot, tmp_path):
    touch_image(tmp_path / "DCIM/Camera/Holiday with Alex.jpg")
    photo = scan_photos(tmp_path)[0]
    cache = ThumbnailCache()
    with qtbot.waitSignal(cache.ready, timeout=5000) as blocker:
        key, path = cache.path_for("dev", photo)
        assert path is None
    assert blocker.args[0] == key and blocker.args[1]
    assert cache.idle()
    assert cache.path_for("dev", photo)[1] is not None  # now served from disk
    assert stat.S_IMODE(thumb_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(cache_root().stat().st_mode) == 0o700
    assert all("Holiday" not in p.name for p in thumb_dir().iterdir())
    assert thumb_key("dev", photo) != thumb_key("other-device", photo)


def test_clear_cache(qtbot, tmp_path):
    touch_image(tmp_path / "DCIM/Camera/a.jpg")
    cache = ThumbnailCache()
    with qtbot.waitSignal(cache.ready, timeout=5000):
        cache.path_for("dev", scan_photos(tmp_path)[0])
    assert cache_size() > 0
    assert clear_cache() == 1
    assert cache_size() == 0 and not cache_root().exists()


def test_scan_of_unreadable_root_raises(tmp_path):
    import pytest

    with pytest.raises(OSError):
        scan_photos(tmp_path / "gone")  # a dead mount must not look like "no photos"
