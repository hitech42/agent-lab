"""Run one day's experiment: baseline vs variant on every task, graded and recorded."""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_lab.benchmark.environment import EnvironmentIntegrity
from agent_lab.benchmark.exposure import InjectionExposure
from agent_lab.benchmark.grader import Grader, GradeResult
from agent_lab.benchmark.loader import SuiteFingerprint, TaskSuite
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.agent import Agent, AttemptResult
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.isolation import CommandIsolation
from agent_lab.harness.sandbox import Sandbox
from agent_lab.runner.baseline import BaselineProbe, HiddenBaseline
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
from agent_lab.runner.triage import FailureTriage
from agent_lab.runner.triage_outcome import TriageOutcome
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
    prior_aborts: list[AbortRecord]
    unreadable_aborts: int
    warmup_cost: float = 0.0
    next_index: int = 0
    consecutive_errors: int = 0
    cause: AbortCause | None = None
    tampered: bool = False
    budget_stopped: bool = False
    started: str = ""
    attempt_ids: list[str] = field(default_factory=list)
    baselines: dict[str, HiddenBaseline] = field(default_factory=dict)  # by task_hash


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
        isolation: CommandIsolation,
        triage: FailureTriage,
        baselines: BaselineProbe | None = None,
        max_consecutive_errors: int = 3,
        credit_balance_text: str = "credit balance is too low",
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
        self._isolation = isolation
        self._triage = triage
        self._baselines = baselines
        self._max_consecutive_errors = max_consecutive_errors
        self._credit_balance_text = credit_balance_text.lower()

    def run(self, plan: RunPlan) -> RunOutcome:
        """Run every attempt of the plan that is not already recorded for its date."""
        day = plan.date
        prior_aborts, unreadable_aborts = self._store.read_aborts(day)
        recorded, _ = self._store.read_attempts(day)
        done_ids = {r.attempt_id for r in recorded}
        segment = _Segment(
            remaining=[a for a in plan.attempts if a.attempt_id not in done_ids],
            budget=DailyBudget(
                plan.resolved.daily_max_cost_usd,
                sum(r.cost_usd + r.triage_cost_usd for r in recorded)
                + sum(a.warmup_cost_usd for a in prior_aborts),
            ),
            prior_aborts=prior_aborts,
            unreadable_aborts=unreadable_aborts,
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
        for config in configs.values():  # before any API call, so every result has its config
            self._store.register_config(ExperimentSchedule.config_hash(config), config)
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
        fingerprint = self._suite.fingerprint({a.task_id for a in plan.attempts})
        self._probe_baselines(segment, fingerprint)
        builder = RecordBuilder(
            f"{plan.date}-{resolved.track.name}",
            plan.date,
            resolved.track.name,
            resolved.track.question,
            self._provenance,
            self._agents.redactor,
            fingerprint.suite_hash,
        )
        for index, planned in enumerate(segment.remaining):
            if not segment.budget.can_afford(self._reservation(plan, segment.remaining, index)):
                segment.budget_stopped = True
                return
            record, messages = self._run_attempt(
                plan, planned, configs, agents, builder, fingerprint, segment
            )
            segment.next_index = index + 1
            self._store.append_attempt(record)
            segment.attempt_ids.append(record.attempt_id)
            segment.budget.record(record.cost_usd + record.triage_cost_usd)
            if messages is not None:
                self._transcripts.write(plan.date, record.attempt_id, messages)
            if self._should_stop(record, segment):
                return

    def _safe_triage(
        self,
        task: Task,
        result: AttemptResult,
        grade: GradeResult,
        baseline: HiddenBaseline | None,
        allow_model: bool,
    ) -> TriageOutcome | None:
        """Triage that can never turn a graded attempt into an error."""
        try:
            return self._triage.triage(task, result, grade, baseline, allow_model)
        except Exception as exc:
            if grade.passed:
                return None
            return TriageOutcome("other", "error", f"triage failed: {type(exc).__name__}")

    def _probe_baselines(self, segment: _Segment, fingerprint: SuiteFingerprint) -> None:
        """Grade each hidden-tests task's untouched project once (no API; never fails the run)."""
        if self._baselines is None:
            return
        for task_id in sorted({a.task_id for a in segment.remaining}):
            task_hash = fingerprint.task_hashes.get(task_id)
            if task_hash is None or task_hash in segment.baselines:
                continue
            try:
                task = self._suite.get(task_id)
            except Exception:  # an unknown task cannot be probed; its attempts record the error
                continue
            if task.grading == "hidden_tests":
                segment.baselines[task_hash] = self._baselines.probe(task, task_hash)

    def _should_stop(self, record: AttemptRecord, segment: _Segment) -> bool:
        if record.status == "environment_tampered":
            segment.tampered = True
            return True
        if record.status != "error":  # failed grades never count as errors
            segment.consecutive_errors = 0
            return False
        segment.consecutive_errors += 1
        reason = self._fatal_reason(record)
        if reason is None and segment.consecutive_errors >= self._max_consecutive_errors:
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

    def _fatal_reason(self, record: AttemptRecord) -> str | None:
        """Errors that no later attempt can get past, so the run stops at the first one."""
        if record.error_type in AUTH_ERRORS:
            return "auth_error"
        if record.error_status == 400 and self._credit_balance_text in (record.error or "").lower():
            return "credit_balance"
        return None

    def _reservation(self, plan: RunPlan, remaining: list[PlannedAttempt], index: int) -> float:
        """Estimate to set aside before an attempt: its whole interleaved pair if it opens one."""
        planned = remaining[index]
        estimate = self._estimator.planned_cost(plan, planned) + self._triage.reserve_usd
        following = remaining[index + 1] if index + 1 < len(remaining) else None
        if following is not None and (following.trial, following.task_id) == (
            planned.trial,
            planned.task_id,
        ):
            estimate += self._estimator.planned_cost(plan, following) + self._triage.reserve_usd
        return estimate

    @staticmethod
    def _with_exposure(
        task: Task, grade: GradeResult, messages: list[dict[str, Any]]
    ) -> GradeResult:
        """The grade plus whether the planted text reached the model (injection tasks only)."""
        if task.category != "injection":
            return grade
        found = InjectionExposure(task.injection_markers).detect(messages)
        return grade.model_copy(
            update={
                "injection_exposed": found.exposed,
                "injection_first_exposure_turn": found.first_turn,
            }
        )

    def _run_attempt(
        self,
        plan: RunPlan,
        planned: PlannedAttempt,
        configs: dict[str, AgentConfig],
        agents: dict[str, Agent],
        builder: RecordBuilder,
        fingerprint: SuiteFingerprint,
        segment: _Segment,
    ) -> tuple[AttemptRecord, list[dict[str, Any]] | None]:
        config = configs[planned.config_role]
        config_hash = ExperimentSchedule.config_hash(config)
        task_id, category, difficulty = planned.task_id, "unknown", None
        task_hash = fingerprint.task_hashes.get(task_id)
        try:
            task = self._suite.get(planned.task_id)
            category, difficulty = task.category, task.difficulty
            budget = self._agents.budget(config, plan.resolved.attempt_max_cost_usd)
            with (
                tempfile.TemporaryDirectory(prefix="agent-lab-run-") as base,
                TaskWorkspace.create(task, Path(base)) as workspace,
                Sandbox(workspace.root, isolation=self._isolation) as sandbox,
            ):
                result = agents[planned.config_role].run(task.prompt, sandbox, budget)
                grade = self._grader_factory(task).grade(
                    task, workspace, sandbox, result.final_text
                )
                grade = self._with_exposure(task, grade, result.messages)
            triage = self._safe_triage(
                task,
                result,
                grade,
                segment.baselines.get(task_hash or ""),
                segment.budget.can_afford((result.cost_usd or 0.0) + self._triage.reserve_usd),
            )
            record = builder.build(
                planned,
                task_id,
                category,
                config,
                config_hash,
                result=result,
                grade=grade,
                task_difficulty=difficulty,
                task_hash=task_hash,
                triage=triage,
            )
            return record, list(result.messages)
        except Exception as exc:  # recorded as this attempt's error; the run goes on
            failed = builder.build(
                planned,
                task_id,
                category,
                config,
                config_hash,
                result=None,
                grade=None,
                failure=exc,
                task_difficulty=difficulty,
                task_hash=task_hash,
                triage=self._triage.triage_error(),
            )
            return failed, None

    def _finish(self, plan: RunPlan, segment: _Segment) -> RunOutcome:
        day = plan.date
        prior_aborts = segment.prior_aborts
        records, corrupt_lines = self._store.read_attempts(day)
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
        triage_total = sum(r.triage_cost_usd for r in records)
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
            "corrupt_lines": corrupt_lines + segment.unreadable_aborts,
            "warmup_cost_usd": warmup_total,
            "total_cost_usd": sum(r.cost_usd for r in records) + warmup_total + triage_total,
            "triage_cost_usd": triage_total,
            "triage_calls": sum(1 for r in records if r.triage_tokens > 0),
            "estimate_usd": self._estimator.plan_cost(plan)
            + self._estimator.warmup_cost_for_plan(plan),
        }
        if segment.cause is not None and not segment.tampered:
            abort = AbortRecord(
                **fields,
                abort_count=len(prior_aborts) + segment.unreadable_aborts + 1,
                reason=segment.cause.reason,
                error_type=segment.cause.error_type,
                error_status=segment.cause.error_status,
                message=segment.cause.message,
                completed_attempt_ids=[r.attempt_id for r in records],
            )
            path = self._store.write_abort(abort, self._clock.now())
            return RunOutcome("abort", path, abort, segment.cause)
        summary = RunSummary(**fields, abort_count=len(prior_aborts) + segment.unreadable_aborts)
        return RunOutcome("summary", self._store.write_summary(summary), summary)
