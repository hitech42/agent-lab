import json
from typing import Any, get_args

import pytest
from pydantic import ValidationError
from runner_support import PROVENANCE, make_plan

from agent_lab.benchmark.grader import GradeResult
from agent_lab.benchmark.manifest import ChangedFiles
from agent_lab.harness.agent import AttemptResult
from agent_lab.harness.agent import AttemptStatus as AgentStatus
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.redact import Redactor
from agent_lab.harness.tracing import AttemptTrace, ToolCallTrace, TurnTrace
from agent_lab.runner.records import (
    AbortRecord,
    AttemptRecord,
    RecordBuilder,
    RecordStatus,
    RunSummary,
    SkippedAttempt,
)

CONFIG = AgentConfig(
    name="baseline",
    model="m",
    effort="high",
    system_prompt="p",
    tool_set="structured",
    max_turns=3,
    max_tokens_per_call=100,
    tool_output_max_chars=100,
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
        planned, "fix-date-range", "bugfix", CONFIG, "abc123abc123",
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
    record = redacting.build(planned, "t", "bugfix", CONFIG, "h", result=result(), grade=leaky)
    assert "LEAK" not in record.detail and "plain-secret" not in record.detail
    failed = redacting.build(
        planned, "t", "bugfix", CONFIG, "h", result=None, grade=None,
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
        "cache_read_tokens", "cost_usd", "effort", "git_dirty", "runner_image", "latency_s",
        "total_latency_s", "retries", "tool_calls",
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


def test_a_record_carries_the_configs_effort_and_the_dirty_flag() -> None:
    record = build()
    assert (record.effort, record.git_dirty) == ("high", False)
    failed = build(result=None, grade=None, failure=RuntimeError("boom"))
    assert failed.effort == "high"  # known from the config even when the attempt failed


def test_fields_added_after_the_first_schema_have_defaults() -> None:
    assert AttemptRecord.model_fields["effort"].default is None
    assert AttemptRecord.model_fields["git_dirty"].default is None
    assert AttemptRecord.model_fields["runner_image"].default is None
    assert RunSummary.model_fields["corrupt_lines"].default == 0


def test_record_statuses_are_the_agents_plus_environment_tampered() -> None:
    assert set(get_args(RecordStatus)) == set(get_args(AgentStatus)) | {"environment_tampered"}


@pytest.mark.parametrize("status", ["truncated", "refused", "incomplete"])
def test_the_new_statuses_are_storable(status: str) -> None:
    data = json.loads(build().model_dump_json())
    assert AttemptRecord.model_validate({**data, "status": status}).status == status


def test_context_calls_are_counted_apart_but_their_tokens_are_in_the_totals() -> None:
    from agent_lab.harness.tracing import ContextCallTrace

    base = trace()
    call = ContextCallTrace(
        kind="summary",
        after_turn=2,
        input_tokens=50,
        output_tokens=7,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=3,
        stop_reason="end_turn",
        latency_s=0.5,
        total_latency_s=0.5,
    )
    with_call = base.model_copy(update={"context_calls": [call]})
    plain = build().model_dump()
    record = build(result=result().model_copy(update={"trace": with_call}))
    assert (record.turns, record.tool_calls) == (plain["turns"], plain["tool_calls"])
    assert record.input_tokens == plain["input_tokens"] + 50
    assert record.output_tokens == plain["output_tokens"] + 7
    assert (record.context_calls, record.context_call_tokens) == (1, 60)
    assert (plain["context_calls"], plain["context_call_tokens"]) == (0, 0)


# --- failure triage fields -------------------------------------------------------------------


def failed_grade() -> GradeResult:
    return grade(passed=False, tests_passed=1, tests_failed=3)


def test_triage_fields_default_to_none_and_zero() -> None:
    record = build()
    assert (record.triage_label, record.triage_source, record.triage_note) == (None, None, None)
    assert (record.triage_cost_usd, record.triage_tokens) == (0.0, 0)


def test_the_builder_copies_a_triage_outcome_into_the_record() -> None:
    from agent_lab.runner.triage_outcome import TriageOutcome

    outcome = TriageOutcome("incomplete_fix", "model", "missed one edge", 0.0004, 2300)
    record = build(grade=failed_grade(), triage=outcome)
    assert (record.triage_label, record.triage_source) == ("incomplete_fix", "model")
    assert (record.triage_note, record.triage_cost_usd, record.triage_tokens) == (
        "missed one edge",
        0.0004,
        2300,
    )
    assert record.cost_usd == 0.0123  # the attempt's own cost is untouched


def test_the_triage_cost_is_not_part_of_the_attempt_cost_or_turns() -> None:
    from agent_lab.runner.triage_outcome import TriageOutcome

    plain = build(grade=failed_grade())
    labelled = build(grade=failed_grade(), triage=TriageOutcome("other", "error", "n", 9.0, 9))
    assert (plain.cost_usd, plain.turns) == (labelled.cost_usd, labelled.turns)


@pytest.mark.parametrize(
    "fields",
    [
        {"triage_label": "other"},  # a label without a source
        {"triage_source": "rule"},  # a source without a label
        {"triage_label": "not_a_label", "triage_source": "rule"},
        {"triage_label": "other", "triage_source": "magic"},
        {"triage_label": "other", "triage_source": "rule", "triage_note": "two\nlines"},
        {"triage_label": "other", "triage_source": "rule", "triage_note": "x" * 201},
        {"triage_note": "a note without a label"},
    ],
)
def test_inconsistent_triage_fields_are_rejected(fields: dict[str, Any]) -> None:
    data = json.loads(build(grade=failed_grade()).model_dump_json())
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate({**data, **fields})


def test_a_passed_attempt_cannot_carry_a_label() -> None:
    data = json.loads(build().model_dump_json())
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate({**data, "triage_label": "other", "triage_source": "rule"})


def test_the_label_enum_is_the_documented_taxonomy() -> None:
    from agent_lab.runner.triage_outcome import FailureLabel

    assert set(get_args(FailureLabel)) == {
        "out_of_turns", "budget_exhausted", "truncated_or_refused", "protected_violation",
        "injection_followed", "environment_tampered", "infrastructure_error", "regression",
        "no_verification", "incomplete_fix", "wrong_approach", "misread_spec", "other",
    }  # fmt: skip


def test_summary_triage_fields_default_for_old_files() -> None:
    summary = RunSummary.model_validate_json(
        (
            __import__("pathlib").Path(__file__).parent / "fixtures" / "run_summary_v1.json"
        ).read_text()
    )
    assert (summary.triage_calls, summary.triage_cost_usd) == (0, 0.0)
