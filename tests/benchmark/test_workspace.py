import os
from pathlib import Path

from task_builders import FIXED_CALC, load_task

from agent_lab.benchmark.workspace import TaskWorkspace


def test_create_copies_project_into_a_fresh_temp_dir(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        assert workspace.root.parent == base.resolve()
        assert workspace.root.name.startswith("task-demo-")
        assert (workspace.root / "calc.py").read_text().startswith("def add")
        assert (workspace.root / "tests" / "test_calc.py").is_file()
        assert not (workspace.root / ".git").exists()
        assert not (workspace.root / "hidden_tests").exists()
        assert not (workspace.root / "solution").exists()
        assert workspace.diff().added == [] and workspace.diff().modified == []


def test_two_workspaces_are_independent(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as a, TaskWorkspace.create(task, base) as b:
        assert a.root != b.root
        (a.root / "calc.py").write_text("changed")
        assert (b.root / "calc.py").read_text().startswith("def add")


def test_does_not_copy_caches_from_the_project(tmp_path: Path) -> None:
    task = load_task(
        tmp_path / "tasks", project={"calc.py": "x = 1\n", "__pycache__/calc.pyc": "junk"}
    )
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        assert not (workspace.root / "__pycache__").exists()


def test_diff_reports_added_modified_deleted(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        (workspace.root / "calc.py").write_text(FIXED_CALC)
        (workspace.root / "tests" / "test_calc.py").unlink()
        (workspace.root / "new.py").write_text("x = 1\n")
        diff = workspace.diff()
    assert diff.added == ["new.py"]
    assert diff.modified == ["calc.py"]
    assert diff.deleted == ["tests/test_calc.py"]


def test_running_tests_does_not_register_as_a_change(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        (workspace.root / "__pycache__").mkdir()
        (workspace.root / "__pycache__" / "calc.cpython-314.pyc").write_bytes(b"x")
        (workspace.root / "tests" / "__pycache__").mkdir()
        (workspace.root / "tests" / "__pycache__" / "t.pyc").write_bytes(b"x")
        (workspace.root / ".pytest_cache").mkdir()
        assert workspace.diff().added == []


def test_overlay_copies_files_over_the_workspace(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        workspace.overlay(task.directory / "solution")
        assert (workspace.root / "calc.py").read_text() == FIXED_CALC
        assert workspace.diff().modified == ["calc.py"]


def test_snapshot_is_a_point_in_time_copy_with_the_original_baseline(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        (workspace.root / "calc.py").write_text(FIXED_CALC)
        with workspace.snapshot(base) as snapshot:
            assert snapshot.root != workspace.root
            # later changes to the original do not reach the snapshot
            (workspace.root / "calc.py").write_text("tampered")
            (workspace.root / "late.txt").write_text("late")
            assert (snapshot.root / "calc.py").read_text() == FIXED_CALC
            assert not (snapshot.root / "late.txt").exists()
            # and its diff is relative to the ORIGINAL baseline
            assert snapshot.diff().modified == ["calc.py"]
            assert snapshot.diff().added == []


def test_snapshot_preserves_symlinks_as_links_and_skips_special_files(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        (workspace.root / "link.txt").symlink_to(outside)
        os.mkfifo(workspace.root / "pipe")  # copying this naively would block forever
        with workspace.snapshot(base) as snapshot:
            link = snapshot.root / "link.txt"
            assert link.is_symlink() and os.readlink(link) == str(outside)
            assert not (snapshot.root / "pipe").exists()


def test_close_removes_the_directory(tmp_path: Path) -> None:
    task = load_task(tmp_path / "tasks")
    base = tmp_path / "base"
    base.mkdir()
    with TaskWorkspace.create(task, base) as workspace:
        root = workspace.root
    assert not root.exists()
