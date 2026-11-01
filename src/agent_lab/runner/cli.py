"""Command-line entry point: ``agent-lab validate-tasks | run | db rebuild``."""

import argparse
import os
import sys
from pathlib import Path
from typing import TextIO

from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskLoadError, TaskSuite
from agent_lab.benchmark.validation import TaskValidator
from agent_lab.harness.isolation import LinuxUserIsolation
from agent_lab.runner.database import ResultsDatabase
from agent_lab.runner.run_command import DbCommand, RunArgs, RunCommand, RunDependencies


class Cli:
    def __init__(
        self,
        validator: TaskValidator,
        run_command: RunCommand,
        db_command: DbCommand,
        stdout: TextIO,
    ) -> None:
        self._validator = validator
        self._run_command = run_command
        self._db_command = db_command
        self._stdout = stdout

    def run(self, argv: list[str]) -> int:
        args = self._parser().parse_args(argv)
        match args.command:
            case "validate-tasks":
                return self._validate_tasks(Path(args.tasks_dir))
            case "run":
                return self._run_command.execute(self._run_args(args))
            case "db":
                return self._db_command.rebuild(Path(args.results_dir), Path(args.db))
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
        run = commands.add_parser("run", help="run today's experiment")
        run.add_argument("--date", help="UTC date YYYY-MM-DD (default: today)")
        run.add_argument("--track", help="track name (default: the date's weekday track)")
        run.add_argument("--trials", type=int, help="trials per task (default: from the schedule)")
        run.add_argument("--tasks", help="comma-separated task ids (default: all)")
        run.add_argument("--results-dir", default="results")
        run.add_argument("--transcripts-dir", default=".transcripts")
        run.add_argument("--tasks-dir", default="tasks")
        run.add_argument("--schedule", default="experiments/schedule.yaml")
        run.add_argument("--resume", action="store_true", help="continue an interrupted day")
        run.add_argument("--dry-run", action="store_true", help="print the plan only")
        run.add_argument(
            "--isolation",
            choices=["none", "linux-user"],
            default="none",
            help="how agent commands are isolated (CI requires linux-user)",
        )
        run.add_argument(
            "--isolation-user",
            default=LinuxUserIsolation.DEFAULT_USER,
            help="the unprivileged user for --isolation linux-user",
        )
        db = commands.add_parser("db", help="derived SQLite database")
        db_commands = db.add_subparsers(dest="db_command", required=True)
        rebuild = db_commands.add_parser("rebuild", help="rebuild results.db from the JSONL")
        rebuild.add_argument("--db", default="results.db")
        rebuild.add_argument("--results-dir", default="results")
        return parser

    @staticmethod
    def _run_args(args: argparse.Namespace) -> RunArgs:
        return RunArgs(
            date=args.date,
            track=args.track,
            trials=args.trials,
            tasks=[t.strip() for t in args.tasks.split(",")] if args.tasks is not None else None,
            results_dir=Path(args.results_dir),
            transcripts_dir=Path(args.transcripts_dir),
            tasks_dir=Path(args.tasks_dir),
            schedule=Path(args.schedule),
            resume=args.resume,
            dry_run=args.dry_run,
            isolation=args.isolation,
            isolation_user=args.isolation_user,
        )


def main() -> None:
    deps = RunDependencies.default(os.environ, Path.cwd())
    cli = Cli(
        TaskValidator(Grader.for_task),
        RunCommand(deps, sys.stdout),
        DbCommand(ResultsDatabase(), sys.stdout),
        sys.stdout,
    )
    sys.exit(cli.run(sys.argv[1:]))
