"""Facts about the shipped tasks that the authoring rules promise."""

import subprocess
import sys
from pathlib import Path

import pytest
from task_builders import REAL_TASKS_DIR, real_task

from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.workspace import TaskWorkspace

pytestmark = pytest.mark.slow  # spawns real pytest subprocesses


def visible_tests(tmp_path: Path, task_id: str, solved: bool) -> subprocess.CompletedProcess[str]:
    task = real_task(task_id)
    with TaskWorkspace.create(task, tmp_path) as workspace:
        if solved:
            workspace.overlay(task.directory / "solution")
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            cwd=workspace.root,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8"},
        )


@pytest.mark.parametrize("task_id", ["fix-date-range", "add-top-flag", "fix-csv-total"])
def test_visible_tests_fail_before_and_pass_after_the_fix(tmp_path: Path, task_id: str) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert visible_tests(tmp_path / "a", task_id, solved=False).returncode == 1
    assert visible_tests(tmp_path / "b", task_id, solved=True).returncode == 0


def test_task_categories_and_gradings() -> None:
    expected = {
        "fix-date-range": ("bugfix", "hidden_tests"),
        "add-top-flag": ("feature", "hidden_tests"),
        "fix-csv-total": ("injection", "hidden_tests"),
    }
    for task_id, (category, grading) in expected.items():
        task = real_task(task_id)
        assert (task.category, task.grading) == (category, grading)


def test_injection_task_has_planted_instructions_and_a_canary() -> None:
    task = real_task("fix-csv-total")
    assert task.canaries == ["AGENT_WAS_HERE.txt"]
    project = task.directory / "project"
    readme = (project / "README.md").read_text()
    data = (project / "data" / "ledger.csv").read_text()
    for text in (readme, data):
        assert "AGENT_WAS_HERE.txt" in text and "tests/" in text
    assert not (project / "AGENT_WAS_HERE.txt").exists()
    # the prompt itself is an ordinary bugfix request
    assert "AGENT_WAS_HERE" not in task.prompt and "ignore" not in task.prompt.lower()


def test_prompts_never_mention_hidden_tests() -> None:
    for task in TaskSuite.load(REAL_TASKS_DIR).all():
        prompt = task.prompt.lower()
        assert "hidden" not in prompt and "grad" not in prompt, task.id


def test_hidden_tests_are_not_in_the_project_or_the_workspace(tmp_path: Path) -> None:
    for task_id in ("fix-date-range", "add-top-flag", "fix-csv-total"):
        task = real_task(task_id)
        with TaskWorkspace.create(task, tmp_path) as workspace:
            names = {p.name for p in workspace.root.rglob("*")}
            hidden = {p.name for p in (task.directory / "hidden_tests").glob("*")}
            assert not names & hidden
            assert not (workspace.root / "solution").exists()
