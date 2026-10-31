import os
from pathlib import Path

import pytest

from agent_lab.benchmark.manifest import DirectoryManifest


def scan(root: Path) -> DirectoryManifest:
    return DirectoryManifest.scan(root)


def test_no_changes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    diff = scan(tmp_path).diff_against(scan(tmp_path))
    assert (diff.added, diff.modified, diff.deleted) == ([], [], [])


def test_added_modified_deleted_are_sorted_relative_posix_paths(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("k")
    (tmp_path / "edit.txt").write_text("old")
    (tmp_path / "gone.txt").write_text("g")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("d")
    before = scan(tmp_path)

    (tmp_path / "edit.txt").write_text("new")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "sub" / "z.txt").write_text("z")
    (tmp_path / "b.txt").write_text("b")
    diff = before.diff_against(scan(tmp_path))

    assert diff.added == ["b.txt", "sub/z.txt"]
    assert diff.modified == ["edit.txt"]
    assert diff.deleted == ["gone.txt"]


def test_same_size_edit_is_detected_by_hash(tmp_path: Path) -> None:
    path = tmp_path / "f.py"
    path.write_text("return a - b\n")
    before = scan(tmp_path)
    stat = path.stat()
    path.write_text("return a + b\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # same size, same mtime
    assert before.diff_against(scan(tmp_path)).modified == ["f.py"]


def test_cache_directories_are_ignored(tmp_path: Path) -> None:
    before = scan(tmp_path)
    for name in ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
        (tmp_path / "tests" / name).mkdir(parents=True)
        (tmp_path / "tests" / name / "x.pyc").write_text("x")
    assert before.diff_against(scan(tmp_path)).added == []


def test_stray_pyc_outside_pycache_is_not_ignored(tmp_path: Path) -> None:
    before = scan(tmp_path)
    (tmp_path / "evil.pyc").write_bytes(b"\0")
    assert before.diff_against(scan(tmp_path)).added == ["evil.pyc"]


def test_symlinks_are_recorded_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    root = tmp_path / "root"
    root.mkdir()
    (root / "dirlink").symlink_to(outside, target_is_directory=True)
    (root / "filelink").symlink_to(outside / "secret.txt")
    before = scan(root)

    (outside / "secret.txt").write_text("changed outside")  # must not show up as a change
    (outside / "new.txt").write_text("n")
    assert before.diff_against(scan(root)).added == []
    assert before.diff_against(scan(root)).modified == []

    (root / "filelink").unlink()
    (root / "filelink").symlink_to(outside / "new.txt")  # retargeting IS a change
    assert before.diff_against(scan(root)).modified == ["filelink"]


def test_dangling_symlink_is_recorded(tmp_path: Path) -> None:
    before = scan(tmp_path)
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    assert before.diff_against(scan(tmp_path)).added == ["dangling"]


def test_fifo_does_not_block_scanning(tmp_path: Path) -> None:
    before = scan(tmp_path)
    os.mkfifo(tmp_path / "pipe")
    assert before.diff_against(scan(tmp_path)).added == ["pipe"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_unreadable_file_is_recorded(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x")
    before = scan(tmp_path)
    path.chmod(0)
    try:
        assert before.diff_against(scan(tmp_path)).modified == ["f.txt"]
    finally:
        path.chmod(0o600)


def test_custom_ignore_dirs(tmp_path: Path) -> None:
    (tmp_path / "skipme").mkdir()
    (tmp_path / "skipme" / "x").write_text("x")
    before = DirectoryManifest.scan(tmp_path, frozenset({"skipme"}))
    (tmp_path / "skipme" / "y").write_text("y")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_text("a")
    after = DirectoryManifest.scan(tmp_path, frozenset({"skipme"}))
    # only the listed directory is ignored; __pycache__ is not when it is not listed
    assert before.diff_against(after).added == ["__pycache__/a.pyc"]
