import json
from typing import Any

import pytest
from pydantic import ValidationError
from runner_support import PROVENANCE, make_plan

from agent_lab.benchmark.grader import GradeResult
from agent_lab.benchmark.manifest import ChangedFiles
from agent_lab.harness.agent import AttemptResult
from agent_lab.harness.redact import Redactor
from agent_lab.harness.tracing import AttemptTrace, ToolCallTrace, TurnTrace
from agent_lab.runner.records import (
    AbortRecord,
    AttemptRecord,
    RecordBuilder,
    RunSummary,
    SkippedAttempt,
)


def builder(redactor: Redactor | None = None) -> RecordBuilder:
    return RecordBuilder(
        "run-1", "2026-11-02", "model-comparison", "Q?", PROVENANCE, redactor or Redactor([])
    )


def trace() -> AttemptTrace:
    def turn(n: int, latency: float, total: float, retries: int) -> TurnTrace:
        return TurnTrace(
            turn=n,
            input_tokens=100 * n,
            output_tokens=10 * n,
            cache_creation_input_tokens=5,
            cache_read_input_tokens=7,
            stop_reason="end_turn",
            latency_s=latency,
            total_latency_s=total,
            retries=retries,
            tool_calls=[
                ToolCallTrace(name="bash", duration_s=0.1, is_error=n == 1),
                ToolCallTrace(name="bash", duration_s=0.1, is_error=False),
            ],
        )

    return AttemptTrace(turns=[turn(1, 1.0, 1.5, 1), turn(2, 2.0, 2.0, 0)])


def result(**changes: Any) -> AttemptResult:
    values: dict[str, Any] = {
        "status": "completed",
        "stop_reason": "end_turn",
        "final_text": "done",
        "trace": trace(),
        "cost_usd": 0.0123,
    }
    return AttemptResult(**{**values, **changes})


def grade(**changes: Any) -> GradeResult:
    values: dict[str, Any] = {
        "passed": True,
        "tests_passed": 4,
        "tests_failed": 0,
        "tests_errors": 0,
        "tests_total": 4,
        "protected_violations": [],
        "canaries_triggered": [],
        "injection_resisted": None,
        "changed_files": ChangedFiles(added=["a.py"], modified=["b.py"], deleted=["c.py"]),
        "detail": "4 passed",
    }
    return GradeResult(**{**values, **changes})


def build(**kwargs: Any) -> AttemptRecord:
    planned = make_plan(trials=1).attempts[0]
    base: dict[str, Any] = {"result": result(), "grade": grade()}
    return builder().build(
        planned, "fix-date-range", "bugfix", "baseline", "abc123abc123", "m", "structured",
        **{**base, **kwargs},
    )  # fmt: skip


def test_a_record_carries_results_usage_and_provenance() -> None:
    record = build()
    assert (record.run_id, record.date, record.track, record.question) == (
        "run-1", "2026-11-02", "model-comparison", "Q?",
    )  # fmt: skip
    assert (record.config_name, record.config_hash, record.model) == (
        "baseline",
        "abc123abc123",
        "m",
    )
    assert (record.task_id, record.task_category) == ("fix-date-range", "bugfix")
    assert (record.status, record.stop_reason, record.schema_version) == (
        "completed",
        "end_turn",
        1,
    )
    assert (record.passed, record.tests_passed, record.tests_total) == (True, 4, 4)
    assert (record.files_added, record.files_modified, record.files_deleted) == (
        ["a.py"], ["b.py"], ["c.py"],
    )  # fmt: skip
    assert (record.turns, record.input_tokens, record.output_tokens) == (2, 300, 30)
    assert (record.cache_write_tokens, record.cache_read_tokens) == (10, 14)
    assert record.cost_usd == 0.0123
    assert record.latency_s == 3.0 and record.total_latency_s == 3.5  # successful calls vs total
    assert (record.retries, record.tool_calls, record.tool_errors) == (1, 4, 1)
    assert (record.git_sha, record.source) == ("abc123", "local")
    assert (record.python_version, record.anthropic_sdk_version) == ("3.14.0", "9.9.9")


def test_tampering_overrides_the_status() -> None:
    record = build(grade=grade(environment_tampered=True, passed=False))
    assert record.status == "environment_tampered" and record.environment_tampered


def test_error_attempts_keep_the_exception_class_and_http_status() -> None:
    bad = result(
        status="error",
        error="RateLimitError: slow down",
        error_type="RateLimitError",
        error_status=429,
    )
    record = build(result=bad)
    assert (record.status, record.error_type, record.error_status) == (
        "error",
        "RateLimitError",
        429,
    )


def test_a_record_built_from_an_exception() -> None:
    record = build(result=None, grade=None, failure=RuntimeError("boom"))
    assert record.status == "error" and record.error == "RuntimeError: boom"
    assert (record.error_type, record.error_status) == ("RuntimeError", None)
    assert (record.passed, record.turns, record.cost_usd, record.tests_total) == (False, 0, 0.0, 0)


def test_free_text_is_redacted() -> None:
    planned = make_plan(trials=1).attempts[0]
    leaky = grade(detail="key sk-ant-api03-LEAK_1 and plain-secret")
    redacting = builder(Redactor(["plain-secret"]))
    record = redacting.build(
        planned, "t", "bugfix", "baseline", "h", "m", "structured", result=result(), grade=leaky
    )
    assert "LEAK" not in record.detail and "plain-secret" not in record.detail
    failed = redacting.build(
        planned, "t", "bugfix", "baseline", "h", "m", "structured", result=None, grade=None,
        failure=RuntimeError("plain-secret"),
    )  # fmt: skip
    assert "plain-secret" not in (failed.error or "")


def test_the_jsonl_schema_round_trips_and_is_flat() -> None:
    record = build()
    line = record.model_dump_json()
    assert "\n" not in line
    assert AttemptRecord.model_validate_json(line) == record
    for key, value in json.loads(line).items():
        assert not isinstance(value, dict), key  # flat: no nested objects
        if isinstance(value, list):
            assert all(isinstance(v, str) for v in value), key  # lists of plain strings


def test_the_expected_columns_are_present() -> None:
    assert set(AttemptRecord.model_fields) >= {
        "schema_version", "run_id", "date", "track", "question", "config_role", "config_name",
        "config_hash", "model", "tool_set", "task_id", "task_category", "trial", "order", "status",
        "stop_reason", "passed", "tests_passed", "tests_failed", "tests_errors", "tests_total",
        "protected_violations", "canaries_triggered", "injection_resisted",
        "environment_tampered", "turns", "input_tokens", "output_tokens", "cache_write_tokens",
        "cache_read_tokens", "cost_usd", "latency_s", "total_latency_s", "retries", "tool_calls",
        "tool_errors", "error", "error_type", "error_status", "git_sha", "uv_lock_sha256",
        "python_version", "anthropic_sdk_version", "source", "attempt_id",
    }  # fmt: skip


def test_unknown_fields_and_bad_statuses_are_rejected() -> None:
    data = json.loads(build().model_dump_json())
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate({**data, "surprise": 1})
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate({**data, "status": "skipped_budget"})  # never stored


def summary_fields() -> dict[str, Any]:
    return {
        "run_id": "r", "date": "2026-11-02", "track": "t", "status": "completed",
        "started": "2026-11-02T09:00:00+00:00", "finished": "2026-11-02T09:05:00+00:00",
        "planned": 6, "completed": 4, "errored": 1, "skipped_budget": 2, "skipped_aborted": 0,
        "skipped_attempts": [
            SkippedAttempt(
                attempt_id="a",
                task_id="x",
                config_role="baseline",
                trial=1,
                reason="skipped_budget",
            )
        ],
        "warmup_cost_usd": 0.001, "total_cost_usd": 0.5, "estimate_usd": 0.7,
    }  # fmt: skip


def test_run_summary_round_trips() -> None:
    summary = RunSummary(**summary_fields())
    assert summary.abort_count == 0 and summary.schema_version == 1
    assert RunSummary.model_validate_json(summary.model_dump_json()) == summary
    dumped = json.loads(summary.model_dump_json())
    assert dumped["skipped_attempts"][0] == {
        "attempt_id": "a", "task_id": "x", "config_role": "baseline", "trial": 1,
        "reason": "skipped_budget",
    }  # fmt: skip


def test_an_abort_record_is_a_summary_plus_the_trigger() -> None:
    abort = AbortRecord(
        **{**summary_fields(), "status": "aborted_error"},
        abort_count=2, reason="auth_error", error_type="AuthenticationError",
        error_status=401, message="bad key", completed_attempt_ids=["a", "b"],
    )  # fmt: skip
    again = AbortRecord.model_validate_json(abort.model_dump_json())
    assert again == abort
    as_summary = RunSummary.model_validate(
        {k: v for k, v in abort.model_dump().items() if k in RunSummary.model_fields}
    )
    assert as_summary.abort_count == 2 and as_summary.status == "aborted_error"
