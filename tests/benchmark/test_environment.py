from pathlib import Path

import pytest

from agent_lab.benchmark.environment import EnvironmentIntegrity


def fake_site(tmp_path: Path) -> Path:
    site = tmp_path / "site-packages"
    (site / "pkg").mkdir(parents=True)
    (site / "pkg" / "__init__.py").write_text("x = 1\n")
    (site / "other.py").write_text("y = 2\n")
    return site


def test_unchanged_environment_verifies_clean(tmp_path: Path) -> None:
    integrity = EnvironmentIntegrity([fake_site(tmp_path)])
    integrity.capture()
    changes = integrity.verify()
    assert (changes.added, changes.modified, changes.deleted) == ([], [], [])


def test_pth_file_written_between_capture_and_verify_is_detected(tmp_path: Path) -> None:
    site = fake_site(tmp_path)
    integrity = EnvironmentIntegrity([site])
    integrity.capture()
    (site / "evil.pth").write_text("import os; os._exit(0)\n")
    changes = integrity.verify()
    assert changes.added == [str((site / "evil.pth").resolve())]
    assert changes.modified == [] and changes.deleted == []


def test_modified_and_deleted_files_and_new_modules_are_detected(tmp_path: Path) -> None:
    site = fake_site(tmp_path)
    integrity = EnvironmentIntegrity([site])
    integrity.capture()
    (site / "pkg" / "__init__.py").write_text("x = 999\n")  # same size, edited source
    (site / "other.py").unlink()
    (site / "pkg" / "new_module.py").write_text("z = 3\n")
    changes = integrity.verify()
    resolved = site.resolve()
    assert changes.added == [str(resolved / "pkg" / "new_module.py")]
    assert changes.modified == [str(resolved / "pkg" / "__init__.py")]
    assert changes.deleted == [str(resolved / "other.py")]


def test_bytecode_caches_are_ignored(tmp_path: Path) -> None:
    site = fake_site(tmp_path)
    integrity = EnvironmentIntegrity([site])
    integrity.capture()
    (site / "pkg" / "__pycache__").mkdir()
    (site / "pkg" / "__pycache__" / "__init__.cpython-314.pyc").write_bytes(b"x")
    assert integrity.verify().added == []


def test_verify_before_capture_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="capture"):
        EnvironmentIntegrity([fake_site(tmp_path)]).verify()


def test_capture_is_computed_once(tmp_path: Path) -> None:
    site = fake_site(tmp_path)
    integrity = EnvironmentIntegrity([site])
    integrity.capture()
    (site / "late.py").write_text("late\n")
    integrity.capture()  # must NOT re-baseline and hide the change
    assert integrity.verify().added == [str((site / "late.py").resolve())]


def test_multiple_site_dirs(tmp_path: Path) -> None:
    first = fake_site(tmp_path / "a")
    second = fake_site(tmp_path / "b")
    integrity = EnvironmentIntegrity([first, second])
    integrity.capture()
    (second / "x.pth").write_text("x\n")
    assert integrity.verify().added == [str((second / "x.pth").resolve())]


def test_running_interpreter_environment_verifies_clean() -> None:
    integrity = EnvironmentIntegrity.for_running_interpreter()
    integrity.capture()
    changes = integrity.verify()
    assert (changes.added, changes.modified, changes.deleted) == ([], [], [])
