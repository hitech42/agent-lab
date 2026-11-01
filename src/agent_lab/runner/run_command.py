"""The `run` and `db` commands: idempotency, planning, dry-run, and running a day."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TextIO

from agent_lab.benchmark.environment import EnvironmentIntegrity
from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskLoadError, TaskSuite, UnknownTaskError
from agent_lab.benchmark.task import Task
from agent_lab.harness.pricing import PriceTable, PricingError
from agent_lab.runner.database import ResultsDatabase
from agent_lab.runner.estimator import CostEstimator
from agent_lab.runner.experiment import Clock, ExperimentRunner, RunOutcome, SystemClock
from agent_lab.runner.plan import PlanOptions, RunPlan, RunPlanner
from agent_lab.runner.provenance import ProvenanceReader
from agent_lab.runner.schedule import ExperimentSchedule, ScheduleError
from agent_lab.runner.storage import ResultsStore, TranscriptStore
from agent_lab.runner.warmup import CacheWarmer
from agent_lab.runner.wiring import AgentFactory, MissingApiKeyError


@dataclass(frozen=True)
class RunArgs:
    date: str | None = None
    track: str | None = None
    trials: int | None = None
    tasks: list[str] | None = None
    results_dir: Path = Path("results")
    transcripts_dir: Path = Path(".transcripts")
    tasks_dir: Path = Path("tasks")
    schedule: Path = Path("experiments/schedule.yaml")
    resume: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class RunDependencies:
    """Everything the run command needs from the outside world (replaced in tests)."""

    clock: Clock
    environ: Mapping[str, str]
    repo_root: Path
    prices: Callable[[], PriceTable]
    agent_factory: Callable[[Mapping[str, str]], AgentFactory]
    integrity: Callable[[], EnvironmentIntegrity]
    grader_factory: Callable[[EnvironmentIntegrity], Callable[[Task], Grader]]

    @classmethod
    def default(cls, environ: Mapping[str, str], repo_root: Path) -> RunDependencies:
        return cls(
            clock=SystemClock(),
            environ=environ,
            repo_root=repo_root,
            prices=lambda: PriceTable.from_yaml(PriceTable.default_path()),
            agent_factory=AgentFactory.from_env,
            integrity=EnvironmentIntegrity.for_running_interpreter,
            grader_factory=lambda integrity: lambda task: Grader.for_task(task, integrity),
        )


class RunCommand:
    """`agent-lab run`: exit 0 whenever a run record was written or the day was done."""

    _DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def __init__(self, deps: RunDependencies, stdout: TextIO) -> None:
        self._deps = deps
        self._out = stdout

    def execute(self, args: RunArgs) -> int:
        try:
            day = self._parse_date(args.date)
        except ValueError as exc:
            return self._error(str(exc))
        problem = self._argument_problem(args)
        if problem is not None:
            return self._error(problem)
        store = ResultsStore(args.results_dir)
        if store.has_summary(day.isoformat()):
            self._say(f"{day}: already done ({store.summary_path(day.isoformat())})")
            return 0
        if store.has_attempts(day.isoformat()) and not args.resume and not args.dry_run:
            return self._error(
                f"{store.attempts_path(day.isoformat())} exists without "
                f"{day}.run.json: an interrupted run. Pass --resume to continue it."
            )
        try:
            prices = self._deps.prices()
            schedule = ExperimentSchedule.from_yaml(args.schedule, prices)
            suite = TaskSuite.load(args.tasks_dir)
            options = PlanOptions(track=args.track, trials=args.trials, tasks=args.tasks)
            plan = RunPlanner(schedule, suite).plan(day, options)
        except (ScheduleError, TaskLoadError, UnknownTaskError, PricingError) as exc:
            return self._error(str(exc))
        if args.resume and not args.dry_run:
            mismatch = self._resume_mismatch(store, plan)
            if mismatch is not None:
                return self._error(mismatch)
        history, corrupt = store.read_all_attempts()
        estimator = CostEstimator(history, schedule.cost_defaults, prices)
        if args.dry_run:
            self._print_plan(plan, estimator, corrupt)
            return 0
        try:
            agents = self._deps.agent_factory(self._deps.environ)
        except MissingApiKeyError as exc:
            return self._error(str(exc))
        integrity = self._deps.integrity()
        runner = ExperimentRunner(
            agents=agents,
            suite=suite,
            grader_factory=self._deps.grader_factory(integrity),
            integrity=integrity,
            store=store,
            transcripts=TranscriptStore(args.transcripts_dir),
            estimator=estimator,
            warmer=CacheWarmer(agents.client, agents.prices),
            provenance=ProvenanceReader(self._deps.repo_root, self._deps.environ).read(),
            clock=self._deps.clock,
        )
        self._print_outcome(runner.run(plan))
        return 0

    @staticmethod
    def _argument_problem(args: RunArgs) -> str | None:
        if args.trials is not None and args.trials < 1:
            return f"--trials must be at least 1, got {args.trials}"
        if args.tasks is not None:
            if any(not task for task in args.tasks):
                return "--tasks must not contain empty task ids"
            duplicates = sorted({t for t in args.tasks if args.tasks.count(t) > 1})
            if duplicates:
                return f"--tasks must not contain duplicates: {', '.join(duplicates)}"
        return None

    @staticmethod
    def _resume_mismatch(store: ResultsStore, plan: RunPlan) -> str | None:
        """Why the day's existing results do not belong to this plan, or None if they do.

        Resuming appends to the same day, so every existing record and readable abort file
        must come from the same run (id and track), and each role must still have the
        config_hash it had: editing the schedule or a prompt between an abort and its
        resume would otherwise mix two configs under one role.
        """
        track = plan.resolved.track.name
        run_id = f"{plan.date}-{track}"
        records, _ = store.read_attempts(plan.date)
        aborts, _ = store.read_aborts(plan.date)
        existing = {(r.run_id, r.track) for r in records} | {(a.run_id, a.track) for a in aborts}
        if existing - {(run_id, track)}:
            found = ", ".join(f"{rid} (track {trk})" for rid, trk in sorted(existing))
            return (
                f"cannot resume {plan.date}: existing results are from {found}, "
                f"but this plan is {run_id} (track {track})"
            )
        expected = {
            "baseline": ExperimentSchedule.config_hash(plan.resolved.baseline),
            "variant": ExperimentSchedule.config_hash(plan.resolved.variant),
        }
        changed = sorted(
            {
                f"{r.config_role} (recorded {r.config_hash}, now {expected[r.config_role]})"
                for r in records
                if r.config_hash != expected[r.config_role]
            }
        )
        if changed:
            return (
                f"cannot resume {plan.date}: the config changed since the first attempts "
                f"were recorded: {', '.join(changed)}"
            )
        return None

    def _parse_date(self, text: str | None) -> date:
        if text is None:
            return self._deps.clock.now().date()
        try:
            if not self._DATE.match(text):
                raise ValueError(text)
            return date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"--date must be a real date as YYYY-MM-DD, got {text!r}") from None

    def _print_plan(self, plan: RunPlan, estimator: CostEstimator, corrupt: int) -> None:
        resolved = plan.resolved
        say = self._say
        say(f"date: {plan.date} (ISO weekday {date.fromisoformat(plan.date).isoweekday()})")
        say(f"track: {resolved.track.name}")
        say(f"question: {resolved.track.question}")
        for role, config in (("baseline", resolved.baseline), ("variant", resolved.variant)):
            say(
                f"{role}: {config.name} hash={ExperimentSchedule.config_hash(config)} "
                f"model={config.model} effort={config.effort} tool_set={config.tool_set} "
                f"max_turns={config.max_turns} max_tokens={config.max_tokens_per_call} "
                f"context={config.context_strategy}"
            )
        say(
            f"trials per task: {resolved.trials_per_task}; daily cap: "
            f"${resolved.daily_max_cost_usd:.2f}; attempt cap: ${resolved.attempt_max_cost_usd:.2f}"
        )
        say(f"plan ({len(plan.attempts)} attempts, in this order):")
        for planned in plan.attempts:
            cost = estimator.planned_cost(plan, planned)
            say(f"  {planned.order:>3}  {planned.attempt_id}  est ${cost:.4f}")
        attempts = estimator.plan_cost(plan)
        warmup = estimator.warmup_cost_for_plan(plan)
        total = attempts + warmup
        say(f"estimate: attempts ${attempts:.4f} + warm-up ${warmup:.4f} = ${total:.4f}")
        if attempts + warmup > resolved.daily_max_cost_usd:
            say("warning: the estimate exceeds the daily cap; expect skipped attempts")
        if corrupt:
            say(f"note: {corrupt} unreadable history line(s) were ignored")
        say("dry run: no API calls were made and no files were written")

    def _print_outcome(self, outcome: RunOutcome) -> None:
        summary = outcome.summary
        say = self._say
        if outcome.kind == "abort":
            cause = outcome.cause
            say(f"ABORTED ({cause.reason if cause else 'unknown'}): wrote {outcome.path}")
            if cause and cause.message:
                say(f"  {cause.error_type or 'error'}: {cause.message}")
            say("  the day is not finished; run again with --resume to continue it")
        else:
            say(f"{summary.date}: {summary.status}, wrote {outcome.path}")
        if summary.corrupt_lines:
            say(
                f"warning: {summary.corrupt_lines} unreadable result line(s)/file(s) for "
                f"{summary.date}; the daily cost may be under-counted"
            )
        say(
            f"  completed {summary.completed}/{summary.planned}, errored {summary.errored}, "
            f"skipped {summary.skipped_budget + summary.skipped_aborted}, "
            f"cost ${summary.total_cost_usd:.4f}"
        )

    def _say(self, text: str) -> None:
        print(text, file=self._out)

    def _error(self, text: str) -> int:
        print(f"error: {text}", file=self._out)
        return 1


class DbCommand:
    """`agent-lab db rebuild`: rebuild the derived SQLite database."""

    def __init__(self, database: ResultsDatabase, stdout: TextIO) -> None:
        self._database = database
        self._out = stdout

    def rebuild(self, results_dir: Path, db_path: Path) -> int:
        report = self._database.rebuild(ResultsStore(results_dir), db_path)
        print(
            f"wrote {db_path}: {report.attempts} attempts, {report.runs} runs, "
            f"{report.aborts} aborts, {report.configs} configs",
            file=self._out,
        )
        if report.corrupt_lines:
            print(
                f"warning: skipped {report.corrupt_lines} unreadable line(s)/file(s)",
                file=self._out,
            )
        return 0
