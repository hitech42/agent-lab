"""Load and validate task fixtures from the tasks directory."""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_lab.benchmark.manifest import DirectoryManifest
from agent_lab.benchmark.task import Task


class TaskLoadError(Exception):
    """One or more tasks are malformed. ``problems`` maps task id to messages."""

    def __init__(self, problems: dict[str, list[str]]) -> None:
        self.problems = problems
        lines = [f"{len(problems)} malformed task(s):"]
        for task_id, messages in problems.items():
            lines += [f"  {task_id}: {message}" for message in messages]
        super().__init__("\n".join(lines))


class UnknownTaskError(KeyError):
    """No task with the requested id is loaded."""


@dataclass(frozen=True)
class SuiteFingerprint:
    """Content hashes (12 hex) of a set of tasks: what a result was measured against."""

    suite_hash: str
    task_hashes: dict[str, str]


class TaskSuite:
    """All tasks found under a tasks directory."""

    _PROMPT_LEAKS = re.compile(r"hidden[\s_-]*tests?|grader|grading", re.IGNORECASE)

    def __init__(self, tasks: dict[str, Task]) -> None:
        self._tasks = dict(tasks)

    @classmethod
    def load(cls, tasks_dir: Path) -> TaskSuite:
        """Load every task directory; raise TaskLoadError listing all malformed ones."""
        if not tasks_dir.is_dir():
            raise TaskLoadError({str(tasks_dir): ["tasks directory does not exist"]})
        tasks: dict[str, Task] = {}
        problems: dict[str, list[str]] = {}
        for entry in sorted(tasks_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            task, messages = cls._load_one(entry)
            if messages:
                problems[entry.name] = messages
            elif task is not None:
                tasks[task.id] = task
        if problems:
            raise TaskLoadError(problems)
        return cls(tasks)

    def get(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError:
            known = ", ".join(sorted(self._tasks)) or "none"
            raise UnknownTaskError(f"unknown task {task_id!r} (known: {known})") from None

    def all(self) -> list[Task]:
        return [self._tasks[task_id] for task_id in sorted(self._tasks)]

    def fingerprint(self, task_ids: Iterable[str] | None = None) -> SuiteFingerprint:
        """Hashes of the given tasks (default: all), so a changed task changes the suite hash.

        A task hash covers its id, ``task.yaml``, ``project/`` and ``hidden_tests/`` (file paths
        and contents, never ``solution/``). The suite hash covers the sorted task ids and task
        hashes. Caches, ``.pyc`` files and ``.DS_Store`` are skipped, so a macOS checkout and CI
        agree; modification times and the checkout location do not matter.
        """
        ids = sorted(set(self._tasks if task_ids is None else task_ids))
        hashes = {task_id: self._task_hash(self.get(task_id)) for task_id in ids}
        return SuiteFingerprint(self._digest(sorted(hashes.items())), hashes)

    @classmethod
    def _task_hash(cls, task: Task) -> str:
        entries = sorted(
            (path, entry.kind, entry.sha256)
            for path, entry in DirectoryManifest.scan(task.directory).entries().items()
            if cls._is_hashed(path)
        )
        return cls._digest([task.id, entries])

    @staticmethod
    def _is_hashed(path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        if name == ".DS_Store" or name.endswith(".pyc"):
            return False
        return path == "task.yaml" or path.startswith((f"{Task.PROJECT}/", f"{Task.HIDDEN_TESTS}/"))

    @staticmethod
    def _digest(value: object) -> str:
        canonical = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]

    @classmethod
    def _load_one(cls, directory: Path) -> tuple[Task | None, list[str]]:
        task = cls._parse_yaml(directory)
        if isinstance(task, list):
            return None, task
        return task, cls._structure_problems(task, directory)

    @staticmethod
    def _parse_yaml(directory: Path) -> Task | list[str]:
        path = directory / "task.yaml"
        if not path.is_file():
            return ["task.yaml is missing"]
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            return [f"task.yaml is not valid YAML: {exc}"]
        if not isinstance(raw, dict):
            return ["task.yaml must contain a mapping"]
        if "directory" in raw:
            return ["'directory' must not appear in task.yaml"]
        try:
            return Task.model_validate({**raw, "directory": directory})
        except ValidationError as exc:
            return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]

    @classmethod
    def _structure_problems(cls, task: Task, directory: Path) -> list[str]:
        problems: list[str] = []
        if task.id != directory.name:
            problems.append(f"id {task.id!r} does not match directory name {directory.name!r}")
        for name in (Task.PROJECT, Task.SOLUTION):
            if not any(p.is_file() for p in (directory / name).rglob("*")):
                problems.append(f"{name}/ is missing or has no files")
        if task.grading == "hidden_tests":
            hidden = directory / Task.HIDDEN_TESTS
            if not any(hidden.glob("test_*.py")):
                problems.append(f"{Task.HIDDEN_TESTS}/ has no test_*.py files")
            if (hidden / "conftest.py").exists():
                problems.append(
                    f"{Task.HIDDEN_TESTS}/conftest.py is not allowed (conftest is ignored)"
                )
        elif not (directory / Task.SOLUTION / Task.ANSWER_FILE).is_file():
            problems.append(f"{Task.SOLUTION}/{Task.ANSWER_FILE} is missing")
        problems += [
            f"symlink not allowed: {p.relative_to(directory).as_posix()}"
            for p in sorted(directory.rglob("*"))
            if p.is_symlink()
        ]
        problems += [
            f"canary {c!r} already exists in {Task.PROJECT}/"
            for c in task.canaries
            if (directory / Task.PROJECT / c).exists()
        ]
        if cls._PROMPT_LEAKS.search(task.prompt):
            problems.append("prompt must not mention hidden tests or grading")
        return problems
