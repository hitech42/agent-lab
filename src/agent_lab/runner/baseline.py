"""Which hidden tests already pass on the untouched project (the input of the regression rule)."""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.isolation import CommandIsolation
from agent_lab.harness.sandbox import Sandbox


@dataclass(frozen=True)
class HiddenBaseline:
    """Ids of the hidden tests that pass on the untouched project of one task.

    ``unavailable`` is a short reason when the pre-run could not tell (then ``passing`` is empty
    and the regression rule is skipped for the task).
    """

    task_hash: str
    passing: frozenset[str] = frozenset()
    unavailable: str | None = None


class BaselineProbe:
    """Grades the untouched project once, with the same grader and isolation as an attempt."""

    def __init__(
        self,
        grader_factory: Callable[[Task], Grader],
        isolation: CommandIsolation,
        scratch_dir: Path | None = None,
        reason_chars: int = 60,
    ) -> None:
        self._grader_factory = grader_factory
        self._isolation = isolation
        self._scratch_dir = scratch_dir
        self._reason_chars = reason_chars

    def probe(self, task: Task, task_hash: str) -> HiddenBaseline:
        """Never raises: any failure is reported as ``unavailable``."""
        try:
            base = self._scratch_dir or Path(tempfile.gettempdir())
            with (
                TaskWorkspace.create(task, base) as workspace,
                Sandbox(workspace.root, isolation=self._isolation) as sandbox,
            ):
                grade = self._grader_factory(task).grade(task, workspace, sandbox, "")
        except Exception as exc:
            return self._unavailable(task_hash, f"{type(exc).__name__}")
        if grade.environment_tampered:
            return self._unavailable(task_hash, "environment changed")
        if grade.tests_total == 0:
            return self._unavailable(task_hash, "no tests collected")
        return HiddenBaseline(task_hash, frozenset(grade.passed_tests))

    def _unavailable(self, task_hash: str, reason: str) -> HiddenBaseline:
        return HiddenBaseline(task_hash, unavailable=reason[: self._reason_chars])
