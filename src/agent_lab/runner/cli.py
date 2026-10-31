"""Command-line entry point: ``agent-lab validate-tasks``."""

import argparse
import sys
from pathlib import Path
from typing import TextIO

from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskLoadError, TaskSuite
from agent_lab.benchmark.validation import TaskValidator


class Cli:
    def __init__(self, validator: TaskValidator, stdout: TextIO) -> None:
        self._validator = validator
        self._stdout = stdout

    def run(self, argv: list[str]) -> int:
        args = self._parser().parse_args(argv)
        match args.command:
            case "validate-tasks":
                return self._validate_tasks(Path(args.tasks_dir))
        return 2  # unreachable: argparse requires a command

    def _validate_tasks(self, tasks_dir: Path) -> int:
        try:
            tasks = TaskSuite.load(tasks_dir).all()
        except TaskLoadError as exc:
            print(exc, file=self._stdout)
            return 1
        if not tasks:
            print(f"no tasks found in {tasks_dir}", file=self._stdout)
            return 1
        failures = warnings = 0
        for task in tasks:
            report = self._validator.validate(task)
            print(f"{'PASS' if report.ok else 'FAIL'} {report.task_id}", file=self._stdout)
            for problem in report.problems:
                print(f"  - {problem}", file=self._stdout)
            for warning in report.warnings:
                print(f"  warning: {warning}", file=self._stdout)
            failures += not report.ok
            warnings += len(report.warnings)
        summary = f"{len(tasks) - failures}/{len(tasks)} tasks valid"
        print(summary + (f", {warnings} warning(s)" if warnings else ""), file=self._stdout)
        return 1 if failures else 0

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="agent-lab")
        commands = parser.add_subparsers(dest="command", required=True)
        validate = commands.add_parser("validate-tasks", help="validate every task fixture")
        validate.add_argument("--tasks-dir", default="tasks")
        return parser


def main() -> None:
    sys.exit(Cli(TaskValidator(Grader.for_task), sys.stdout).run(sys.argv[1:]))
