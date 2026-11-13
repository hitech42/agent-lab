"""Check that a task is well formed: solvable, but not already solved."""

import ast
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, Field

from agent_lab.benchmark.exposure import normalize
from agent_lab.benchmark.grader import Grader, GradeResult
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.sandbox import Sandbox


class ValidationReport(BaseModel):
    """Problems fail validation; warnings are only reported."""

    task_id: str
    ok: bool
    problems: list[str]
    warnings: list[str] = Field(default_factory=list)


class TaskValidator:
    """Validates tasks without any API access.

    For every task: grading fails on the untouched project, passes with solution/
    applied, the protected and canary checks are clean for the solution, the visible
    tests pass on the solved project, task code imports only the standard library and
    pytest, and the project's code stays within the line limit of its difficulty tier
    (``max_code_lines``: 200 for easy and medium, 600 for hard). A project under 50 lines only
    produces a warning.
    """

    MIN_CODE_LINES = 50
    DEFAULT_MAX_CODE_LINES: Mapping[str, int] = MappingProxyType(
        {"easy": 200, "medium": 200, "hard": 600}
    )

    def __init__(
        self,
        grader_factory: Callable[[Task], Grader],
        base_dir: Path | None = None,
        visible_timeout_s: float = 120.0,
        max_code_lines: Mapping[str, int] = DEFAULT_MAX_CODE_LINES,
    ) -> None:
        self._grader_factory = grader_factory
        self._base_dir = base_dir
        self._visible_timeout_s = visible_timeout_s
        self._max_code_lines = dict(max_code_lines)

    def validate(self, task: Task) -> ValidationReport:
        problems = [
            *self._check_constraints(task),
            *self._check_untouched(task),
            *self._check_solved(task),
        ]
        return ValidationReport(
            task_id=task.id,
            ok=not problems,
            problems=problems,
            warnings=self._size_warnings(task),
        )

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
        limit = self._max_code_lines[task.difficulty]
        if lines > limit:
            problems.append(
                f"project has {lines} lines of code outside tests/; "
                f"maximum for a {task.difficulty} task is {limit}"
            )
        problems += self.marker_problems(task)
        return problems

    @staticmethod
    def marker_problems(task: Task) -> list[str]:
        """Each marker is planted in a project file, is not in the prompt or a canary path."""
        project = task.directory / Task.PROJECT
        texts: list[str] = []
        for path in sorted(project.rglob("*")):
            if path.is_file():
                try:
                    texts.append(normalize(path.read_text()))
                except UnicodeDecodeError, OSError:
                    continue  # binary files cannot carry planted text the agent could read
        prompt = normalize(task.prompt)
        canaries = [normalize(c) for c in task.canaries]
        problems: list[str] = []
        for marker in task.injection_markers:
            key = normalize(marker)
            if not any(key in text for text in texts):
                problems.append(f"injection marker {marker!r} occurs in no file under project/")
            if key in prompt:
                problems.append(f"injection marker {marker!r} occurs in the prompt")
            if any(key in canary for canary in canaries):
                problems.append(f"injection marker {marker!r} is part of a canary path")
        return problems

    def _size_warnings(self, task: Task) -> list[str]:
        lines = self._code_lines(task.directory / Task.PROJECT)
        if lines < self.MIN_CODE_LINES:
            return [
                f"project has only {lines} lines of code outside tests/; "
                f"{self.MIN_CODE_LINES}-{self._max_code_lines[task.difficulty]} is the target"
            ]
        return []

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
