"""Dead-mount detection. Never touches real mounts: paths are tmp dirs or fake mount tables."""

import errno
import os

from mooring.services import sshfs

MOUNTS = (
    "proc /proc proc rw 0 0\n"
    "kdeconnect@192.0.2.10:/ /run/user/1000/abc fuse.sshfs rw,nosuid 0 0\n"
    "x /run/user/1000/with\\040space fuse.sshfs rw 0 0\n"
)


def test_is_sshfs_mount_parses_mount_table():
    assert sshfs.is_sshfs_mount("/run/user/1000/abc", MOUNTS)
    assert sshfs.is_sshfs_mount("/run/user/1000/with space", MOUNTS)  # octal-escaped
    assert not sshfs.is_sshfs_mount("/proc", MOUNTS)  # not sshfs
    assert not sshfs.is_sshfs_mount("/run/user/1000/other", MOUNTS)


def test_healthy_directory_is_not_dead(tmp_path):
    assert not sshfs.is_dead(str(tmp_path))


def test_eio_counts_as_dead(tmp_path, monkeypatch):
    def broken_stat(path):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(sshfs.os, "stat", broken_stat)
    assert sshfs.is_dead(str(tmp_path))


def test_missing_path_is_not_dead(tmp_path):
    assert not sshfs.is_dead(str(tmp_path / "nope"))  # ENOENT: not a dead mount


def test_refuses_paths_outside_the_runtime_dir(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(sshfs.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    assert not sshfs.clear_stale_mount("/home/someone/data")
    assert not sshfs.clear_stale_mount(str(tmp_path / "runtime"))  # the dir itself
    assert called == []


def test_unmounts_only_a_dead_sshfs_mount(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    target = runtime / "abc"
    target.mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr(sshfs.subprocess, "run", lambda args, **k: calls.append(args) or Done())
    monkeypatch.setattr(sshfs.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sshfs, "is_sshfs_mount", lambda path, text=None: path == str(target))

    monkeypatch.setattr(sshfs, "is_dead", lambda path, timeout=3.0: False)
    assert not sshfs.clear_stale_mount(str(target))  # healthy mount: leave it alone
    monkeypatch.setattr(sshfs, "is_dead", lambda path, timeout=3.0: True)
    assert sshfs.clear_stale_mount(str(target))
    assert calls == [["/usr/bin/fusermount3", "-u", "-z", os.path.realpath(target)]]
