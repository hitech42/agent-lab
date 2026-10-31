from pathlib import Path

import pytest
from task_builders import REAL_TASKS_DIR, write_task

from agent_lab.benchmark.loader import TaskLoadError, TaskSuite, UnknownTaskError


def test_loads_tasks_sorted_and_gets_by_id(tmp_path: Path) -> None:
    write_task(tmp_path, "zebra")
    write_task(tmp_path, "alpha")
    suite = TaskSuite.load(tmp_path)
    assert [t.id for t in suite.all()] == ["alpha", "zebra"]
    task = suite.get("alpha")
    assert task.directory == tmp_path / "alpha"
    assert task.protected == ["tests/**"]


def test_unknown_task_lists_known_ids(tmp_path: Path) -> None:
    write_task(tmp_path, "alpha")
    with pytest.raises(UnknownTaskError, match="known: alpha"):
        TaskSuite.load(tmp_path).get("nope")


def test_skips_files_and_dot_directories(tmp_path: Path) -> None:
    write_task(tmp_path, "alpha")
    (tmp_path / "README.md").write_text("docs")
    (tmp_path / ".hidden").mkdir()
    assert [t.id for t in TaskSuite.load(tmp_path).all()] == ["alpha"]


def test_missing_tasks_dir(tmp_path: Path) -> None:
    with pytest.raises(TaskLoadError, match="does not exist"):
        TaskSuite.load(tmp_path / "nope")


def test_all_malformed_tasks_are_reported_together(tmp_path: Path) -> None:
    write_task(tmp_path, "good")
    write_task(tmp_path, "bad-one", category="nonsense")
    (tmp_path / "bad-two").mkdir()
    with pytest.raises(TaskLoadError) as excinfo:
        TaskSuite.load(tmp_path)
    assert set(excinfo.value.problems) == {"bad-one", "bad-two"}
    assert "2 malformed task(s)" in str(excinfo.value)
    assert "category" in str(excinfo.value) and "task.yaml is missing" in str(excinfo.value)


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("invalid yaml", "not valid YAML"),
        ("list yaml", "must contain a mapping"),
        ("directory key", "'directory' must not appear"),
    ],
)
def test_bad_task_yaml(tmp_path: Path, setup: str, expected: str) -> None:
    directory = write_task(tmp_path, "demo")
    yaml_path = directory / "task.yaml"
    if setup == "invalid yaml":
        yaml_path.write_text("id: [unclosed\n")
    elif setup == "list yaml":
        yaml_path.write_text("- a\n- b\n")
    else:
        yaml_path.write_text(yaml_path.read_text() + "directory: /tmp\n")
    with pytest.raises(TaskLoadError, match=expected):
        TaskSuite.load(tmp_path)


def test_id_must_match_directory(tmp_path: Path) -> None:
    directory = write_task(tmp_path, "demo")
    directory.rename(tmp_path / "other")
    with pytest.raises(TaskLoadError, match="does not match directory name"):
        TaskSuite.load(tmp_path)


def test_missing_project_and_solution(tmp_path: Path) -> None:
    write_task(tmp_path, "no-project", project={})
    write_task(tmp_path, "no-solution", solution={})
    with pytest.raises(TaskLoadError) as excinfo:
        TaskSuite.load(tmp_path)
    assert "project/ is missing or has no files" in str(excinfo.value)
    assert "solution/ is missing or has no files" in str(excinfo.value)


def test_hidden_tests_must_exist_and_not_use_conftest(tmp_path: Path) -> None:
    write_task(tmp_path, "no-hidden", hidden={})
    write_task(tmp_path, "with-conftest", hidden={"test_x.py": "", "conftest.py": ""})
    with pytest.raises(TaskLoadError) as excinfo:
        TaskSuite.load(tmp_path)
    assert "no test_*.py files" in str(excinfo.value)
    assert "conftest.py is not allowed" in str(excinfo.value)


def test_exact_answer_needs_expected_answer_and_solution_answer_file(tmp_path: Path) -> None:
    write_task(
        tmp_path,
        "qa-ok",
        category="qa",
        grading="exact_answer",
        yaml_extra="expected_answer: '42'\n",
    )
    suite = TaskSuite.load(tmp_path)
    assert suite.get("qa-ok").expected_answer == "42"

    write_task(
        tmp_path,
        "qa-bad",
        category="qa",
        grading="exact_answer",
        yaml_extra="expected_answer: '42'\n",
        solution={"other.txt": "x"},
    )
    with pytest.raises(TaskLoadError, match=r"solution/ANSWER.txt is missing"):
        TaskSuite.load(tmp_path)


def test_symlinks_are_rejected(tmp_path: Path) -> None:
    directory = write_task(tmp_path, "demo")
    (directory / "project" / "link.py").symlink_to(directory / "project" / "calc.py")
    with pytest.raises(TaskLoadError, match=r"symlink not allowed: project/link\.py"):
        TaskSuite.load(tmp_path)


def test_canary_must_not_exist_in_project(tmp_path: Path) -> None:
    write_task(
        tmp_path,
        "demo",
        yaml_extra="canaries: [calc.py]\n",
    )
    with pytest.raises(TaskLoadError, match=r"canary 'calc\.py' already exists"):
        TaskSuite.load(tmp_path)


@pytest.mark.parametrize(
    "prompt",
    [
        "Make the hidden tests pass.",
        "Hidden-tests will check this.",
        "This is graded by the grader.",
        "We are grading you.",
        "Do not touch hidden_test files.",
    ],
)
def test_prompts_must_not_mention_hidden_tests_or_grading(tmp_path: Path, prompt: str) -> None:
    write_task(tmp_path, "demo", prompt=prompt)
    with pytest.raises(TaskLoadError, match="must not mention hidden tests or grading"):
        TaskSuite.load(tmp_path)


def test_real_tasks_load() -> None:
    ids = [t.id for t in TaskSuite.load(REAL_TASKS_DIR).all()]
    assert ids == ["add-top-flag", "fix-csv-total", "fix-date-range"]
