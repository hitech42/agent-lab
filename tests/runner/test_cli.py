import io
from pathlib import Path

import pytest
from task_builders import BUGGY_CALC, FIXED_CALC, REAL_TASKS_DIR, VISIBLE_TEST, write_task

from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.validation import TaskValidator
from agent_lab.runner.cli import Cli


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = Cli(TaskValidator(Grader.for_task), out).run(argv)
    return code, out.getvalue()


@pytest.mark.slow
def test_validate_tasks_on_the_real_tasks() -> None:
    code, output = run(["validate-tasks", "--tasks-dir", str(REAL_TASKS_DIR)])
    assert code == 0
    assert "PASS fix-date-range" in output and "PASS add-top-flag" in output
    assert "PASS fix-csv-total" in output
    assert "3/3 tasks valid" in output


def test_validate_tasks_reports_malformed_tasks(tmp_path: Path) -> None:
    write_task(tmp_path, "bad", category="nonsense")
    code, output = run(["validate-tasks", "--tasks-dir", str(tmp_path)])
    assert code == 1
    assert "1 malformed task(s)" in output and "bad:" in output


@pytest.mark.slow
def test_small_projects_print_a_warning_but_still_pass(tmp_path: Path) -> None:
    write_task(tmp_path, "tiny")  # valid, but far under the 50-line target
    code, output = run(["validate-tasks", "--tasks-dir", str(tmp_path)])
    assert code == 0
    assert "PASS tiny" in output
    assert "warning: project has only" in output
    assert "1/1 tasks valid, 1 warning(s)" in output


@pytest.mark.slow
def test_oversized_projects_fail(tmp_path: Path) -> None:
    filler = "\n".join(f"V{i} = {i}" for i in range(300)) + "\n"
    write_task(
        tmp_path,
        "huge",
        project={"calc.py": BUGGY_CALC + filler, "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": FIXED_CALC + filler},
    )
    code, output = run(["validate-tasks", "--tasks-dir", str(tmp_path)])
    assert code == 1
    assert "FAIL huge" in output and "maximum is 200" in output
    assert "0/1 tasks valid" in output


def test_validate_tasks_with_no_tasks_fails(tmp_path: Path) -> None:
    code, output = run(["validate-tasks", "--tasks-dir", str(tmp_path)])
    assert code == 1 and "no tasks found" in output


def test_missing_tasks_dir_fails(tmp_path: Path) -> None:
    code, output = run(["validate-tasks", "--tasks-dir", str(tmp_path / "nope")])
    assert code == 1 and "does not exist" in output


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as excinfo:
        run([])
    assert excinfo.value.code == 2
