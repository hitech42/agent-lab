"""Run hidden pytest tests in a hardened subprocess and report honest counts.

Hardening (threat model: the agent can create, modify or delete any file in the
workspace before grading):
- ``python -I``: no cwd/script dir on sys.path, no PYTHON* env vars, no user site.
- The workspace is appended to sys.path *after* stdlib and site-packages, so no
  workspace file can shadow stdlib, pytest or its dependencies.
- ``-X pycache_prefix`` ignores workspace ``__pycache__`` (stale bytecode).
- ``--noconftest``, an explicit ``-c`` config outside the workspace, explicit
  ``--rootdir``, ``-p no:cacheprovider`` and ``--disable-plugin-autoload``.
- The JUnit report is written outside the workspace and parsed defensively.
"""

import shlex
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel

from agent_lab.harness.sandbox import Sandbox


class PytestOutcome(BaseModel):
    """Result of one hardened pytest run (data only)."""

    ok: bool
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    detail: str


class PytestRunner:
    _RUNNER = (
        "import sys; sys.path.append(sys.argv.pop(1)); "
        "import pytest; sys.exit(pytest.main(sys.argv[1:]))"
    )
    _MAX_REPORT_BYTES = 1_000_000
    _DETAIL_CHARS = 2000

    def __init__(self, timeout_s: float = 120.0, python: str = sys.executable) -> None:
        self._timeout_s = timeout_s
        self._python = python

    def run(self, sandbox: Sandbox, tests_dir: Path) -> PytestOutcome:
        """Run the tests under ``tests_dir`` (inside sandbox.root) and score them."""
        with tempfile.TemporaryDirectory(prefix="agent-lab-pytest-") as scratch_name:
            scratch = Path(scratch_name)
            (scratch / "grade.ini").write_text("[pytest]\n")
            result = sandbox.run_command(
                self._command(sandbox.root, tests_dir, scratch), self._timeout_s
            )
            if result.timed_out:
                return self._failure(f"hidden tests timed out after {self._timeout_s:g}s")
            return self._score(scratch / "junit.xml", result.exit_code, result.output)

    def _command(self, root: Path, tests_dir: Path, scratch: Path) -> str:
        args = [
            self._python,
            "-I",
            "-X",
            f"pycache_prefix={scratch / 'pyc'}",
            "-c",
            self._RUNNER,
            str(root),
            "--noconftest",
            "-p",
            "no:cacheprovider",
            "--disable-plugin-autoload",
            "--import-mode=importlib",
            "-c",
            str(scratch / "grade.ini"),
            f"--rootdir={root}",
            f"--junitxml={scratch / 'junit.xml'}",
            f"--basetemp={scratch / 'tmp'}",
            "--tb=short",
            "-q",
            str(tests_dir),
        ]
        return " ".join(shlex.quote(a) for a in args)

    def _score(self, report: Path, exit_code: int | None, output: str) -> PytestOutcome:
        tail = output[-self._DETAIL_CHARS :]
        cases = self._read_cases(report)
        if isinstance(cases, str):
            return self._failure(f"{cases}\n{tail}")
        passed = sum(1 for c in cases if c == "passed")
        failed, errors, skipped = (cases.count(k) for k in ("failed", "error", "skipped"))
        problems = []
        if not cases:
            problems.append("no tests were collected")
        if exit_code != 0:
            problems.append(f"pytest exit code {exit_code}")
        elif failed or errors or skipped:
            problems.append("exit code 0 contradicts the report")
        if passed != len(cases):
            problems.append("not every test passed (skipped tests do not count)")
        detail = "; ".join(problems) + ("\n" if problems else "") + tail
        return PytestOutcome(
            ok=not problems,
            total=len(cases),
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            detail=detail[: self._DETAIL_CHARS * 2],
        )

    def _read_cases(self, report: Path) -> list[str] | str:
        """Outcome per testcase ('passed'/'failed'/'error'/'skipped'), or an error message."""
        if not report.is_file():
            return "no JUnit report was written (process exited early or crashed)"
        if report.stat().st_size > self._MAX_REPORT_BYTES:
            return "JUnit report is too large"
        data = report.read_bytes()
        if b"<!doctype" in data.lower() or b"<!entity" in data.lower():
            return "JUnit report contains a DTD/entity declaration"
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            return f"JUnit report is not valid XML: {exc}"
        cases: list[str] = []
        for case in root.iter("testcase"):
            if case.find("failure") is not None:
                cases.append("failed")
            elif case.find("error") is not None:
                cases.append("error")
            elif case.find("skipped") is not None:
                cases.append("skipped")
            else:
                cases.append("passed")
        return cases

    @staticmethod
    def _failure(detail: str) -> PytestOutcome:
        return PytestOutcome(
            ok=False, total=0, passed=0, failed=0, errors=0, skipped=0, detail=detail
        )
