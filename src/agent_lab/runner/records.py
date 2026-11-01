"""Result records: one flat row per attempt, one summary per run, one file per abort."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_lab.benchmark.grader import GradeResult
from agent_lab.harness.agent import AttemptResult
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.redact import Redactor
from agent_lab.runner.plan import PlannedAttempt
from agent_lab.runner.provenance import Provenance

SCHEMA_VERSION = 1
RecordStatus = Literal[
    "completed",
    "truncated",
    "refused",
    "incomplete",
    "max_turns",
    "budget_exceeded",
    "error",
    "environment_tampered",
]
RunStatus = Literal["completed", "partial_budget", "aborted_tampered", "aborted_error"]
SkipReason = Literal["skipped_budget", "skipped_aborted"]


class AttemptRecord(BaseModel):
    """One attempt, flat (no nested objects), as written to results/runs/<date>.jsonl."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    run_id: str
    date: str
    track: str
    question: str
    attempt_id: str
    config_role: str
    config_name: str
    config_hash: str
    model: str
    tool_set: str
    effort: str | None = None  # absent in records written before effort existed
    task_id: str
    task_category: str
    trial: int
    order: int
    status: RecordStatus
    stop_reason: str | None
    # GradeResult, flattened
    passed: bool
    tests_passed: int
    tests_failed: int
    tests_errors: int
    tests_total: int
    protected_violations: list[str]
    canaries_triggered: list[str]
    injection_resisted: bool | None
    environment_tampered: bool
    files_added: list[str]
    files_modified: list[str]
    files_deleted: list[str]
    detail: str
    # usage
    turns: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: float
    latency_s: float
    total_latency_s: float
    retries: int
    tool_calls: int
    tool_errors: int
    error: str | None
    error_type: str | None
    error_status: int | None
    # provenance
    git_sha: str
    git_dirty: bool | None = None  # None: unknown (git unavailable) or an older record
    uv_lock_sha256: str
    python_version: str
    anthropic_sdk_version: str
    source: str


class ConfigRecord(BaseModel):
    """A resolved AgentConfig as stored in results/configs/<config_hash>.json (flat, data only).

    Every AgentConfig field except ``name`` is present, prompt text included: exactly what
    ``config_hash`` covers, so the hash can be recomputed from the file. ``first_name`` is
    only the name the config had when it was first registered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    config_hash: str
    first_name: str
    model: str
    system_prompt: str
    effort: str
    tool_set: str
    max_turns: int
    max_tokens_per_call: int
    tool_output_max_chars: int
    context_strategy: str


class SkippedAttempt(BaseModel):
    """An attempt that never ran, with enough detail to stand without the plan."""

    model_config = ConfigDict(frozen=True)

    attempt_id: str
    task_id: str
    config_role: str
    trial: int
    reason: SkipReason


class RunSummary(BaseModel):
    """One day's run. Written as <date>.run.json only for terminal outcomes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    run_id: str
    date: str
    track: str
    status: RunStatus
    started: str
    finished: str
    planned: int
    completed: int
    errored: int
    skipped_budget: int
    skipped_aborted: int
    skipped_attempts: list[SkippedAttempt] = Field(default_factory=list)
    abort_count: int = 0
    corrupt_lines: int = 0  # unreadable result lines/abort files found for this date
    warmup_cost_usd: float
    total_cost_usd: float
    estimate_usd: float


class AbortRecord(RunSummary):
    """An abort event (<date>.abort-<HHMMSS>.json): a summary plus what triggered it."""

    reason: str
    error_type: str | None = None
    error_status: int | None = None
    message: str | None = None
    completed_attempt_ids: list[str] = Field(default_factory=list)


class RecordBuilder:
    """Builds AttemptRecords for one run from the plan item, config and results."""

    def __init__(
        self,
        run_id: str,
        date: str,
        track: str,
        question: str,
        provenance: Provenance,
        redactor: Redactor,
    ) -> None:
        self._redactor = redactor
        self._run_id = run_id
        self._date = date
        self._track = track
        self._question = question
        self._provenance = provenance

    def build(
        self,
        planned: PlannedAttempt,
        task_id: str,
        task_category: str,
        config: AgentConfig,
        config_hash: str,
        result: AttemptResult | None,
        grade: GradeResult | None,
        failure: Exception | None = None,
    ) -> AttemptRecord:
        """A record from a finished attempt, or from the exception that stopped it."""
        values: dict[str, Any] = {}
        if result is not None and grade is not None:
            values = self._from_results(result, grade)
        else:
            values = self._from_failure(failure)
        return AttemptRecord(
            run_id=self._run_id,
            date=self._date,
            track=self._track,
            question=self._question,
            attempt_id=planned.attempt_id,
            config_role=planned.config_role,
            config_name=config.name,
            config_hash=config_hash,
            model=config.model,
            tool_set=config.tool_set,
            effort=config.effort,
            task_id=task_id,
            task_category=task_category,
            trial=planned.trial,
            order=planned.order,
            **self._redacted(values),
            **self._provenance.model_dump(),
        )

    def _redacted(self, values: dict[str, Any]) -> dict[str, Any]:
        """Free-text fields go through the redactor before they reach a results file."""
        for key in ("detail", "error"):
            if isinstance(values.get(key), str):
                values[key] = self._redactor.redact(values[key])
        return values

    @staticmethod
    def _from_results(result: AttemptResult, grade: GradeResult) -> dict[str, Any]:
        trace = result.trace
        return {
            "status": "environment_tampered" if grade.environment_tampered else result.status,
            "stop_reason": result.stop_reason,
            "passed": grade.passed,
            "tests_passed": grade.tests_passed,
            "tests_failed": grade.tests_failed,
            "tests_errors": grade.tests_errors,
            "tests_total": grade.tests_total,
            "protected_violations": grade.protected_violations,
            "canaries_triggered": grade.canaries_triggered,
            "injection_resisted": grade.injection_resisted,
            "environment_tampered": grade.environment_tampered,
            "files_added": grade.changed_files.added,
            "files_modified": grade.changed_files.modified,
            "files_deleted": grade.changed_files.deleted,
            "detail": grade.detail,
            "turns": len(trace.turns),
            "input_tokens": trace.total_input_tokens,
            "output_tokens": trace.total_output_tokens,
            "cache_write_tokens": trace.total_cache_creation_input_tokens,
            "cache_read_tokens": trace.total_cache_read_input_tokens,
            "cost_usd": result.cost_usd or 0.0,
            "latency_s": sum(t.latency_s for t in trace.turns),
            "total_latency_s": trace.total_latency_s,
            "retries": trace.total_retries,
            "tool_calls": trace.tool_call_count,
            "tool_errors": trace.tool_error_count,
            "error": result.error,
            "error_type": result.error_type,
            "error_status": result.error_status,
        }

    @staticmethod
    def _from_failure(failure: Exception | None) -> dict[str, Any]:
        name = type(failure).__name__ if failure else "UnknownError"
        return {
            "status": "error",
            "stop_reason": None,
            "passed": False,
            "tests_passed": 0,
            "tests_failed": 0,
            "tests_errors": 0,
            "tests_total": 0,
            "protected_violations": [],
            "canaries_triggered": [],
            "injection_resisted": None,
            "environment_tampered": False,
            "files_added": [],
            "files_modified": [],
            "files_deleted": [],
            "detail": "",
            "turns": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
            "latency_s": 0.0,
            "total_latency_s": 0.0,
            "retries": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "error": f"{name}: {failure}",
            "error_type": name,
            "error_status": getattr(failure, "status_code", None),
        }
