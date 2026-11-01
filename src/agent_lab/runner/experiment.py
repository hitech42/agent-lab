"""Run one day's experiment: baseline vs variant on every task, graded and recorded."""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_lab.benchmark.environment import EnvironmentIntegrity
from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.agent import Agent
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.sandbox import Sandbox
from agent_lab.runner.budget import DailyBudget
from agent_lab.runner.estimator import CostEstimator
from agent_lab.runner.plan import PlannedAttempt, RunPlan
from agent_lab.runner.provenance import Provenance
from agent_lab.runner.records import (
    AbortRecord,
    AttemptRecord,
    RecordBuilder,
    RunStatus,
    RunSummary,
    SkippedAttempt,
    SkipReason,
)
from agent_lab.runner.schedule import ExperimentSchedule
from agent_lab.runner.storage import ResultsStore, TranscriptStore
from agent_lab.runner.warmup import AbortCause, CacheWarmer
from agent_lab.runner.wiring import AgentFactory

AUTH_ERRORS = frozenset({"AuthenticationError", "PermissionDeniedError"})


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class RunOutcome:
    """What a run wrote: a terminal summary (<date>.run.json) or an abort event."""

    kind: Literal["summary", "abort"]
    path: Path
    summary: RunSummary  # an AbortRecord when kind == "abort"
    cause: AbortCause | None = None


@dataclass
class _Segment:
    """Mutable state of one invocation of run() (one day may take several)."""

    remaining: list[PlannedAttempt]
    budget: DailyBudget
    warmup_cost: float = 0.0
    next_index: int = 0
    consecutive_errors: int = 0
    cause: AbortCause | None = None
    tampered: bool = False
    budget_stopped: bool = False
    started: str = ""
    attempt_ids: list[str] = field(default_factory=list)


class ExperimentRunner:
    """Orchestrates a RunPlan: warm up, run attempts, record each one immediately.

    A crash mid-run keeps everything already recorded. Aborts (warm-up failure, an
    authentication error, repeated errors) are events: they write an abort file and the
    day stays resumable. Tampering and budget exhaustion are terminal.
    """

    def __init__(
        self,
        agents: AgentFactory,
        suite: TaskSuite,
        grader_factory: Callable[[Task], Grader],
        integrity: EnvironmentIntegrity,
        store: ResultsStore,
        transcripts: TranscriptStore,
        estimator: CostEstimator,
        warmer: CacheWarmer,
        provenance: Provenance,
        clock: Clock,
        max_consecutive_errors: int = 3,
    ) -> None:
        self._agents = agents
        self._suite = suite
        self._grader_factory = grader_factory
        self._integrity = integrity
        self._store = store
        self._transcripts = transcripts
        self._estimator = estimator
        self._warmer = warmer
        self._provenance = provenance
        self._clock = clock
        self._max_consecutive_errors = max_consecutive_errors

    def run(self, plan: RunPlan) -> RunOutcome:
        """Run every attempt of the plan that is not already recorded for its date."""
        day = plan.date
        prior_aborts = self._store.read_aborts(day)
        recorded, _ = self._store.read_attempts(day)
        done_ids = {r.attempt_id for r in recorded}
        segment = _Segment(
            remaining=[a for a in plan.attempts if a.attempt_id not in done_ids],
            budget=DailyBudget(
                plan.resolved.daily_max_cost_usd,
                sum(r.cost_usd for r in recorded) + sum(a.warmup_cost_usd for a in prior_aborts),
            ),
            started=self._clock.now().isoformat(timespec="seconds"),
        )
        try:
            self._execute(plan, segment)
        except Exception as exc:  # a bug outside any attempt: stop cleanly, keep the day resumable
            segment.cause = AbortCause(
                reason="internal_error",
                error_type=type(exc).__name__,
                error_status=getattr(exc, "status_code", None),
                message=str(exc),
            )
        return self._finish(plan, segment)

    def _execute(self, plan: RunPlan, segment: _Segment) -> None:
        resolved = plan.resolved
        configs = {"baseline": resolved.baseline, "variant": resolved.variant}
        agents = {role: self._agents.agent(config) for role, config in configs.items()}
        self._integrity.capture()
        roles = {a.config_role for a in segment.remaining}
        to_warm = {
            ExperimentSchedule.config_hash(configs[role]): agents[role] for role in sorted(roles)
        }
        if to_warm:
            warmup = self._warmer.warm(to_warm)
            segment.warmup_cost = warmup.cost_usd
            segment.budget.record(warmup.cost_usd)
            if warmup.failure is not None:
                segment.cause = warmup.failure
                return
        builder = RecordBuilder(
            f"{plan.date}-{resolved.track.name}",
            plan.date,
            resolved.track.name,
            resolved.track.question,
            self._provenance,
            self._agents.redactor,
        )
        for index, planned in enumerate(segment.remaining):
            if not segment.budget.can_afford(self._reservation(plan, segment.remaining, index)):
                segment.budget_stopped = True
                return
            record, messages = self._run_attempt(plan, planned, configs, agents, builder)
            segment.next_index = index + 1
            self._store.append_attempt(record)
            segment.attempt_ids.append(record.attempt_id)
            segment.budget.record(record.cost_usd)
            if messages is not None:
                self._transcripts.write(plan.date, record.attempt_id, messages)
            if self._should_stop(record, segment):
                return

    def _should_stop(self, record: AttemptRecord, segment: _Segment) -> bool:
        if record.status == "environment_tampered":
            segment.tampered = True
            return True
        if record.status != "error":  # failed grades never count as errors
            segment.consecutive_errors = 0
            return False
        segment.consecutive_errors += 1
        reason = None
        if record.error_type in AUTH_ERRORS:
            reason = "auth_error"
        elif segment.consecutive_errors >= self._max_consecutive_errors:
            reason = "consecutive_errors"
        if reason is None:
            return False
        segment.cause = AbortCause(
            reason=reason,
            error_type=record.error_type,
            error_status=record.error_status,
            message=record.error,
        )
        return True

    def _reservation(self, plan: RunPlan, remaining: list[PlannedAttempt], index: int) -> float:
        """Estimate to set aside before an attempt: its whole interleaved pair if it opens one."""
        planned = remaining[index]
        estimate = self._estimator.planned_cost(plan, planned)
        following = remaining[index + 1] if index + 1 < len(remaining) else None
        if following is not None and (following.trial, following.task_id) == (
            planned.trial,
            planned.task_id,
        ):
            estimate += self._estimator.planned_cost(plan, following)
        return estimate

    def _run_attempt(
        self,
        plan: RunPlan,
        planned: PlannedAttempt,
        configs: dict[str, AgentConfig],
        agents: dict[str, Agent],
        builder: RecordBuilder,
    ) -> tuple[AttemptRecord, list[dict[str, Any]] | None]:
        config = configs[planned.config_role]
        config_hash = ExperimentSchedule.config_hash(config)
        task_id, category = planned.task_id, "unknown"
        try:
            task = self._suite.get(planned.task_id)
            category = task.category
            budget = self._agents.budget(config, plan.resolved.attempt_max_cost_usd)
            with (
                tempfile.TemporaryDirectory(prefix="agent-lab-run-") as base,
                TaskWorkspace.create(task, Path(base)) as workspace,
                Sandbox(workspace.root) as sandbox,
            ):
                result = agents[planned.config_role].run(task.prompt, sandbox, budget)
                grade = self._grader_factory(task).grade(
                    task, workspace, sandbox, result.final_text
                )
            record = builder.build(
                planned,
                task_id,
                category,
                config.name,
                config_hash,
                config.model,
                config.tool_set,
                result=result,
                grade=grade,
            )
            return record, list(result.messages)
        except Exception as exc:  # recorded as this attempt's error; the run goes on
            failed = builder.build(
                planned,
                task_id,
                category,
                config.name,
                config_hash,
                config.model,
                config.tool_set,
                result=None,
                grade=None,
                failure=exc,
            )
            return failed, None

    def _finish(self, plan: RunPlan, segment: _Segment) -> RunOutcome:
        day = plan.date
        prior_aborts = self._store.read_aborts(day)
        records, _ = self._store.read_attempts(day)
        unrun = segment.remaining[segment.next_index :]
        stopped = segment.cause is not None or segment.tampered
        reason: SkipReason = "skipped_aborted" if stopped else "skipped_budget"
        skipped = [
            SkippedAttempt(
                attempt_id=a.attempt_id,
                task_id=a.task_id,
                config_role=a.config_role,
                trial=a.trial,
                reason=reason,
            )
            for a in unrun
        ]
        warmup_total = sum(a.warmup_cost_usd for a in prior_aborts) + segment.warmup_cost
        status: RunStatus = "completed"
        if segment.tampered:
            status = "aborted_tampered"
        elif segment.cause is not None:
            status = "aborted_error"
        elif segment.budget_stopped:
            status = "partial_budget"
        fields: dict[str, Any] = {
            "run_id": f"{day}-{plan.resolved.track.name}",
            "date": day,
            "track": plan.resolved.track.name,
            "status": status,
            "started": min([a.started for a in prior_aborts] + [segment.started]),
            "finished": self._clock.now().isoformat(timespec="seconds"),
            "planned": len(plan.attempts),
            "completed": len(records),
            "errored": sum(1 for r in records if r.status == "error"),
            "skipped_budget": len(skipped) if reason == "skipped_budget" else 0,
            "skipped_aborted": len(skipped) if reason == "skipped_aborted" else 0,
            "skipped_attempts": skipped,
            "warmup_cost_usd": warmup_total,
            "total_cost_usd": sum(r.cost_usd for r in records) + warmup_total,
            "estimate_usd": self._estimator.plan_cost(plan)
            + self._estimator.warmup_cost_for_plan(plan),
        }
        if segment.cause is not None and not segment.tampered:
            abort = AbortRecord(
                **fields,
                abort_count=len(prior_aborts) + 1,
                reason=segment.cause.reason,
                error_type=segment.cause.error_type,
                error_status=segment.cause.error_status,
                message=segment.cause.message,
                completed_attempt_ids=[r.attempt_id for r in records],
            )
            path = self._store.write_abort(abort, self._clock.now())
            return RunOutcome("abort", path, abort, segment.cause)
        summary = RunSummary(**fields, abort_count=len(prior_aborts))
        return RunOutcome("summary", self._store.write_summary(summary), summary)
