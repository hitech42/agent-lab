import py_compile
import subprocess
import sys
from pathlib import Path

import pytest
from task_builders import BUGGY_CALC, FIXED_CALC

from agent_lab.benchmark.pytest_runner import PytestRunner
from agent_lab.harness.sandbox import Sandbox

HIDDEN_PASS = "def test_ok():\n    assert 1 + 1 == 2\n"
HIDDEN_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def run(
    tmp_path: Path, files: dict[str, str], runner: PytestRunner | None = None
) -> tuple[object, Path]:
    root = tmp_path / "ws"
    hidden = root / "grade_0123456789abcdef"
    hidden.mkdir(parents=True)
    for rel, content in files.items():
        path = hidden / rel if rel.startswith("test_") else root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    with Sandbox(root) as sandbox:
        return (runner or PytestRunner()).run(sandbox, hidden), root


def outcome(tmp_path: Path, files: dict[str, str], runner: PytestRunner | None = None):  # type: ignore[no-untyped-def]
    return run(tmp_path, files, runner)[0]


def test_passing_tests(tmp_path: Path) -> None:
    result = outcome(tmp_path, {"test_a.py": HIDDEN_PASS})
    assert (result.ok, result.total, result.passed, result.failed) == (True, 1, 1, 0)


def test_failing_test(tmp_path: Path) -> None:
    result = outcome(tmp_path, {"test_a.py": "def test_bad():\n    assert 1 == 2\n"})
    assert (result.ok, result.total, result.passed, result.failed) == (False, 1, 0, 1)
    assert "assert 1 == 2" in result.detail


def test_counts_a_mix_of_outcomes(tmp_path: Path) -> None:
    src = (
        "import pytest\n"
        "def test_p():\n    pass\n"
        "def test_f():\n    assert False\n"
        "def test_s():\n    pytest.skip('no')\n"
        "@pytest.fixture\ndef broken():\n    raise RuntimeError('boom')\n"
        "def test_e(broken):\n    pass\n"
    )
    result = outcome(tmp_path, {"test_a.py": src})
    assert (result.total, result.passed, result.failed, result.skipped, result.errors) == (
        4,
        1,
        1,
        1,
        1,
    )
    assert not result.ok


def test_skipped_tests_do_not_count_as_passing(tmp_path: Path) -> None:
    result = outcome(
        tmp_path, {"test_a.py": "import pytest\n\ndef test_s():\n    pytest.skip('x')\n"}
    )
    assert not result.ok and result.skipped == 1


def test_zero_collected_tests_is_a_failure(tmp_path: Path) -> None:
    result = outcome(tmp_path, {"test_a.py": "x = 1\n"})
    assert not result.ok and result.total == 0
    assert "no tests were collected" in result.detail


def test_collection_error_is_a_failure(tmp_path: Path) -> None:
    result = outcome(tmp_path, {"test_a.py": "import module_that_does_not_exist\n"})
    assert not result.ok and result.errors >= 1


def test_timeout_is_a_failure(tmp_path: Path) -> None:
    src = "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    result = outcome(tmp_path, {"test_a.py": src}, PytestRunner(timeout_s=1.0))
    assert not result.ok and "timed out after 1s" in result.detail


def test_premature_exit_without_a_report_is_a_failure(tmp_path: Path) -> None:
    result = outcome(tmp_path, {"test_a.py": "import os\nos._exit(0)\n"})
    assert not result.ok and "no JUnit report" in result.detail


def test_project_module_importable_from_workspace_root(tmp_path: Path) -> None:
    result = outcome(tmp_path, {"calc.py": FIXED_CALC, "test_a.py": HIDDEN_CALC})
    assert result.ok
    result = outcome(tmp_path / "again", {"calc.py": BUGGY_CALC, "test_a.py": HIDDEN_CALC})
    assert not result.ok and result.failed == 1


FORGERY = """
import atexit, sys

def _forge():
    path = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--junitxml=")][0]
    open(path, "w").write({payload!r})

atexit.register(_forge)

def test_ok():
    pass
"""


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><testsuite/>', "DTD/entity"),
        ("<testsuite", "not valid XML"),
        ("<testsuite>" + "<!-- pad -->" * 120_000 + "</testsuite>", "too large"),
    ],
)
def test_forged_or_hostile_reports_are_rejected(
    tmp_path: Path, payload: str, expected: str
) -> None:
    result = outcome(tmp_path, {"test_a.py": FORGERY.format(payload=payload)})
    assert not result.ok and expected in result.detail


def test_report_that_contradicts_the_exit_code_is_rejected(tmp_path: Path) -> None:
    payload = (
        '<testsuite tests="1"><testcase classname="t" name="test_x"><failure message="m"/>'
        "</testcase></testsuite>"
    )
    result = outcome(tmp_path, {"test_a.py": FORGERY.format(payload=payload)})
    assert not result.ok and "contradicts the report" in result.detail


def test_stale_bytecode_in_the_workspace_is_ignored(tmp_path: Path) -> None:
    # An unchecked-hash .pyc of the BUGGY source loads regardless of the (fixed) source,
    # as would a stale timestamp-based pyc after a same-size, same-second edit.
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(BUGGY_CALC)
    cache = root / "__pycache__"
    cache.mkdir()
    py_compile.compile(
        str(root / "calc.py"),
        cfile=str(cache / f"calc.{sys.implementation.cache_tag}.pyc"),
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    (root / "calc.py").write_text(FIXED_CALC)

    # control: a normal interpreter really does load the stale bytecode
    probe = f"import sys; sys.path.insert(0, {str(root)!r}); import calc; print(calc.add(2, 3))"
    stale = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert stale.stdout.strip() == "-1"

    hidden = root / "grade_0123456789abcdef"
    hidden.mkdir()
    (hidden / "test_a.py").write_text(HIDDEN_CALC)
    with Sandbox(root) as sandbox:
        assert PytestRunner().run(sandbox, hidden).ok
