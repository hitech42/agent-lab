from pathlib import Path

import pytest
from task_builders import (
    BUGGY_CALC,
    FIXED_CALC,
    REAL_TASKS_DIR,
    VISIBLE_TEST,
    load_task,
    real_task,
)

from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.validation import TaskValidator, ValidationReport

pytestmark = pytest.mark.slow  # spawns real pytest subprocesses


REAL_IDS = [t.id for t in TaskSuite.load(REAL_TASKS_DIR).all()]
PADDING = "\n".join(f"V{i} = {i}" for i in range(60)) + "\n"  # lifts the project to 50+ code lines


def validate(task: Task) -> ValidationReport:
    return TaskValidator(Grader.for_task).validate(task)


def padded(source: str) -> str:
    return source + PADDING


@pytest.mark.parametrize("task_id", REAL_IDS)
def test_real_tasks_are_valid(task_id: str) -> None:
    report = validate(real_task(task_id))
    assert report.ok, report.problems
    assert report.problems == []


def test_a_well_formed_synthetic_task_is_valid(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        project={"calc.py": padded(BUGGY_CALC), "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": padded(FIXED_CALC)},
    )
    assert validate(task).problems == []


def test_solution_that_does_not_fix_the_bug_is_reported(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        project={"calc.py": padded(BUGGY_CALC), "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": padded(BUGGY_CALC)},
    )
    problems = validate(task).problems
    assert any("grading fails with solution/ applied" in p for p in problems)


def test_project_that_is_already_solved_is_reported(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        project={"calc.py": padded(FIXED_CALC), "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": padded(FIXED_CALC)},
    )
    assert any("already solved" in p for p in validate(task).problems)


def test_hidden_tests_that_collect_nothing_are_reported(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        project={"calc.py": padded(BUGGY_CALC), "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": padded(FIXED_CALC)},
        hidden={"test_hidden.py": "x = 1\n"},
    )
    problems = validate(task).problems
    assert any("collected nothing" in p for p in problems)


def test_solution_touching_protected_files_is_reported(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        project={"calc.py": padded(BUGGY_CALC), "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": padded(FIXED_CALC), "tests/test_calc.py": VISIBLE_TEST + "# edit\n"},
    )
    assert any("solution touches protected files" in p for p in validate(task).problems)


def test_solution_triggering_a_canary_is_reported(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        category="injection",
        yaml_extra="canaries: [PWNED.txt]\n",
        project={"calc.py": padded(BUGGY_CALC), "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": padded(FIXED_CALC), "PWNED.txt": "x"},
    )
    problems = validate(task).problems
    assert any("triggers canaries" in p for p in problems)
    assert any("does not resist the injection" in p for p in problems)


def test_visible_tests_failing_on_the_solved_project_are_reported(tmp_path: Path) -> None:
    failing_visible = "from calc import add\n\n\ndef test_wrong():\n    assert add(1, 1) == 3\n"
    task = load_task(
        tmp_path,
        project={"calc.py": padded(BUGGY_CALC), "tests/test_calc.py": failing_visible},
        solution={"calc.py": padded(FIXED_CALC)},
    )
    assert "visible tests fail on the solved project" in validate(task).problems


def test_project_without_visible_tests_skips_that_check(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        project={"calc.py": padded(BUGGY_CALC)},
        solution={"calc.py": padded(FIXED_CALC)},
    )
    assert validate(task).problems == []


def test_third_party_imports_are_reported(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        project={
            "calc.py": padded("import requests\n" + BUGGY_CALC),
            "tests/test_calc.py": VISIBLE_TEST,
        },
        solution={"calc.py": padded("import numpy\n" + FIXED_CALC)},
        hidden={
            "test_hidden.py": (
                "import pandas\n\nfrom calc import add\n\n"
                "def test_a():\n    assert add(2, 3) == 5\n"
            )
        },
    )
    problems = validate(task).problems
    assert any("project/calc.py imports 'requests'" in p for p in problems)
    assert any("solution/calc.py imports 'numpy'" in p for p in problems)
    assert any("hidden_tests/test_hidden.py imports 'pandas'" in p for p in problems)


def test_stdlib_pytest_and_project_imports_are_allowed(tmp_path: Path) -> None:
    helper = "def helper():\n    return 1\n"
    task = load_task(
        tmp_path,
        project={
            "calc.py": padded(
                "import json\nfrom collections import Counter\nfrom helper import helper\n"
                + BUGGY_CALC
            ),
            "helper.py": helper,
            "tests/test_calc.py": "import pytest\n" + VISIBLE_TEST,
        },
        solution={
            "calc.py": padded(
                "import json\nfrom collections import Counter\nfrom helper import helper\n"
                + FIXED_CALC
            )
        },
    )
    assert validate(task).problems == []


def sized_task(tmp_path: Path, extra_lines: int) -> Task:
    filler = "\n".join(f"V{i} = {i}" for i in range(extra_lines)) + "\n"
    return load_task(
        tmp_path,
        project={"calc.py": BUGGY_CALC + filler, "tests/test_calc.py": VISIBLE_TEST},
        solution={"calc.py": FIXED_CALC + filler},
    )


def test_project_under_the_minimum_size_is_only_a_warning(tmp_path: Path) -> None:
    report = validate(sized_task(tmp_path, 0))
    assert report.ok and report.problems == []
    assert len(report.warnings) == 1
    assert "only 2 lines of code" in report.warnings[0] and "50-200" in report.warnings[0]


def test_project_within_bounds_has_no_warning(tmp_path: Path) -> None:
    report = validate(sized_task(tmp_path, 60))
    assert report.ok and report.warnings == []


def test_project_over_the_maximum_size_is_an_error(tmp_path: Path) -> None:
    report = validate(sized_task(tmp_path, 400))
    assert not report.ok
    assert any("maximum is 200" in p for p in report.problems)
    assert report.warnings == []


def test_exact_answer_task_validates(tmp_path: Path) -> None:
    task = load_task(
        tmp_path,
        category="qa",
        grading="exact_answer",
        yaml_extra="expected_answer: '42'\n",
        project={
            "notes.py": padded("ANSWER_HINT = 'ask the oracle'\n"),
            "tests/test_n.py": "def test_n():\n    pass\n",
        },
    )
    assert validate(task).problems == []


def test_exact_answer_task_whose_expected_answer_does_not_match_the_solution_is_reported(
    tmp_path: Path,
) -> None:
    task = load_task(
        tmp_path,
        category="qa",
        grading="exact_answer",
        yaml_extra="expected_answer: '43'\n",
        project={"notes.py": padded("X = 1\n")},
    )
    assert any("grading fails with solution/ applied" in p for p in validate(task).problems)
