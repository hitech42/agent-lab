"""Builders for tiny synthetic tasks and access to the real ones (no fixtures: see below).

This is a plain module, not a conftest.py: tests/harness already has a conftest.py and
two sibling conftest modules would shadow each other's ``from conftest import ...``.
"""

from pathlib import Path

from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.task import Task

REAL_TASKS_DIR = Path(__file__).resolve().parents[2] / "tasks"

BUGGY_CALC = "def add(a, b):\n    return a - b\n"
FIXED_CALC = "def add(a, b):\n    return a + b\n"
VISIBLE_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n"
HIDDEN_TEST = "from calc import add\n\n\ndef test_add_hidden():\n    assert add(2, 3) == 5\n"


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def write_task(
    tasks_dir: Path,
    task_id: str = "demo",
    *,
    category: str = "bugfix",
    grading: str = "hidden_tests",
    prompt: str = "Fix the add function.",
    yaml_extra: str = "",
    project: dict[str, str] | None = None,
    hidden: dict[str, str] | None = None,
    solution: dict[str, str] | None = None,
) -> Path:
    """Create tasks_dir/<task_id>/ with a tiny buggy `calc` project; return its path."""
    directory = tasks_dir / task_id
    directory.mkdir(parents=True)
    yaml = (
        f"id: {task_id}\ntitle: Demo task\ncategory: {category}\n"
        f"prompt: {prompt!r}\ngrading: {grading}\n{yaml_extra}"
    )
    (directory / "task.yaml").write_text(yaml)
    write_files(
        directory / "project",
        project
        if project is not None
        else {"calc.py": BUGGY_CALC, "tests/test_calc.py": VISIBLE_TEST},
    )
    if grading == "hidden_tests":
        write_files(
            directory / "hidden_tests",
            hidden if hidden is not None else {"test_hidden.py": HIDDEN_TEST},
        )
    default_solution = {"calc.py": FIXED_CALC}
    if grading == "exact_answer":
        default_solution = {"ANSWER.txt": "42\n"}
    write_files(directory / "solution", solution if solution is not None else default_solution)
    return directory


def load_task(tasks_dir: Path, task_id: str = "demo", **kwargs: object) -> Task:
    write_task(tasks_dir, task_id, **kwargs)  # type: ignore[arg-type]
    return TaskSuite.load(tasks_dir).get(task_id)


def real_task(task_id: str) -> Task:
    return TaskSuite.load(REAL_TASKS_DIR).get(task_id)
