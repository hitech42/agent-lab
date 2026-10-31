"""Check that a task is well formed: solvable, but not already solved."""

import ast
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from agent_lab.benchmark.grader import Grader, GradeResult
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.sandbox import Sandbox


class ValidationReport(BaseModel):
    task_id: str
    ok: bool
    problems: list[str]


class TaskValidator:
    """Validates tasks without any API access.

    For every task: grading fails on the untouched project, passes with solution/
    applied, the protected and canary checks are clean for the solution, the visible
    tests pass on the solved project, task code imports only the standard library and
    pytest, and the project's code is 50-200 lines.
    """

    MIN_CODE_LINES = 50
    MAX_CODE_LINES = 200

    def __init__(
        self,
        grader_factory: Callable[[Task], Grader],
        base_dir: Path | None = None,
        visible_timeout_s: float = 120.0,
    ) -> None:
        self._grader_factory = grader_factory
        self._base_dir = base_dir
        self._visible_timeout_s = visible_timeout_s

    def validate(self, task: Task) -> ValidationReport:
        problems = [
            *self._check_constraints(task),
            *self._check_untouched(task),
            *self._check_solved(task),
        ]
        return ValidationReport(task_id=task.id, ok=not problems, problems=problems)

    def _grade(self, task: Task, apply_solution: bool) -> tuple[GradeResult, bool | None]:
        """Grade the untouched or solved project; also run visible tests when solved."""
        with TaskWorkspace.create(task, self._scratch_base()) as workspace:
            if apply_solution:
                workspace.overlay(task.directory / Task.SOLUTION)
            with Sandbox(workspace.root) as sandbox:
                result = self._grader_factory(task).grade(task, workspace, sandbox, "")
                visible = self._visible_tests_pass(workspace, sandbox) if apply_solution else None
        return result, visible

    def _scratch_base(self) -> Path:
        return Path(tempfile.gettempdir()) if self._base_dir is None else self._base_dir

    def _check_constraints(self, task: Task) -> list[str]:
        problems: list[str] = []
        directories = [Task.PROJECT, Task.HIDDEN_TESTS, Task.SOLUTION]
        local = {
            p.stem if p.is_file() else p.name
            for name in (Task.PROJECT, Task.SOLUTION)
            for p in (task.directory / name).iterdir()
        }
        allowed = sys.stdlib_module_names | {"pytest"} | local
        for name in directories:
            for path in sorted((task.directory / name).rglob("*.py")):
                for module in self._imported_modules(path):
                    if module not in allowed:
                        rel = path.relative_to(task.directory).as_posix()
                        problems.append(f"{rel} imports {module!r}: only stdlib and pytest allowed")
        lines = self._code_lines(task.directory / Task.PROJECT)
        if not self.MIN_CODE_LINES <= lines <= self.MAX_CODE_LINES:
            problems.append(
                f"project has {lines} lines of code outside tests/; "
                f"expected {self.MIN_CODE_LINES}-{self.MAX_CODE_LINES}"
            )
        return problems

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
        return modules

    @staticmethod
    def _code_lines(project: Path) -> int:
        total = 0
        for path in project.rglob("*.py"):
            if "tests" in path.relative_to(project).parts:
                continue
            total += sum(
                1
                for line in path.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
        return total

    def _check_untouched(self, task: Task) -> list[str]:
        result, _ = self._grade(task, apply_solution=False)
        problems: list[str] = []
        if result.passed:
            problems.append("grading passes on the untouched project (task is already solved)")
        if task.grading == "hidden_tests" and result.tests_total == 0:
            problems.append(
                f"hidden tests collected nothing on the untouched project: {result.detail}"
            )
        return problems

    def _check_solved(self, task: Task) -> list[str]:
        result, visible = self._grade(task, apply_solution=True)
        problems: list[str] = []
        if not result.passed:
            problems.append(f"grading fails with solution/ applied: {result.detail}")
        if result.protected_violations:
            problems.append(f"solution touches protected files: {result.protected_violations}")
        if result.canaries_triggered:
            problems.append(f"solution triggers canaries: {result.canaries_triggered}")
        if task.category == "injection" and result.injection_resisted is not True:
            problems.append("solution does not resist the injection")
        if visible is False:
            problems.append("visible tests fail on the solved project")
        return problems

    def _visible_tests_pass(self, workspace: TaskWorkspace, sandbox: Sandbox) -> bool | None:
        """True/False if the project has visible tests, None if it has none."""
        if not any(workspace.root.rglob("test_*.py")):
            return None
        result = sandbox.run_command(
            "python -m pytest -q -p no:cacheprovider", self._visible_timeout_s
        )
        return result.exit_code == 0 and not result.timed_out
