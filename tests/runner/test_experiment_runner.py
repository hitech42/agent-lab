import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from runner_support import (
    MONDAY,
    PROVENANCE,
    SUNDAY,
    FakeGrader,
    FixedClock,
    auth_error,
    build_world,
    default_responder,
    is_warmup,
    make_plan,
    prices,
    status_error,
    text_response,
    tool_use_response,
    usage,
    with_caps,
)

from agent_lab.harness.redact import Redactor
from agent_lab.harness.tracing import TokenUsage
from agent_lab.runner.records import AttemptRecord
from agent_lab.runner.schedule import ExperimentSchedule
from agent_lab.runner.storage import ResultsStore


def attempt_counter() -> Any:
    """A responder factory: (is_failing(n) -> response) with n = 1-based attempt number."""

    def make(failing: Any):  # type: ignore[no-untyped-def]
        state = {"n": 0}

        def responder(request: dict[str, Any]) -> Any:
            if is_warmup(request):
                return default_responder(request)
            state["n"] += 1
            outcome = failing(state["n"])
            return default_responder(request) if outcome is None else outcome

        return responder

    return make


make_responder = attempt_counter()


def records_of(world: Any, day: str = MONDAY) -> list[AttemptRecord]:
    return world.store.read_attempts(day)[0]


# --- a normal day ---------------------------------------------------------------------


def test_a_full_day_records_every_attempt_and_writes_a_terminal_summary(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    outcome = world.runner.run(world.plan)

    assert outcome.kind == "summary" and outcome.summary.status == "completed"
    assert outcome.path == world.store.summary_path(MONDAY)
    summary = world.store.read_summaries()[0]
    assert (summary.planned, summary.completed, summary.errored) == (6, 6, 0)
    assert (summary.skipped_budget, summary.skipped_aborted, summary.skipped_attempts) == (0, 0, [])
    assert summary.abort_count == 0 and not world.store.read_aborts()
    assert summary.run_id == "2026-11-02-model-comparison" and summary.track == "model-comparison"
    assert summary.started <= summary.finished
    records = records_of(world)
    assert [r.attempt_id for r in records] == [a.attempt_id for a in world.plan.attempts]
    warmup = sum(
        prices().cost(
            m, TokenUsage(input_tokens=10, output_tokens=1, cache_creation_input_tokens=500)
        )
        for m in ("claude-haiku-5-5", "claude-sonnet-5-5")
    )
    assert summary.warmup_cost_usd == pytest.approx(warmup)
    assert summary.total_cost_usd == pytest.approx(sum(r.cost_usd for r in records) + warmup)
    assert summary.estimate_usd > 0


def test_each_record_has_the_documented_fields(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.runner.run(world.plan)
    plan_by_id = {a.attempt_id: a for a in world.plan.attempts}
    for record in records_of(world):
        planned = plan_by_id[record.attempt_id]
        config = (
            world.plan.resolved.baseline
            if planned.config_role == "baseline"
            else world.plan.resolved.variant
        )
        assert (record.config_role, record.trial, record.order) == (
            planned.config_role,
            planned.trial,
            planned.order,
        )
        assert record.config_hash == ExperimentSchedule.config_hash(config)
        assert (record.config_name, record.model, record.tool_set) == (
            config.name,
            config.model,
            config.tool_set,
        )
        assert record.date == MONDAY and record.track == "model-comparison"
        assert "sonnet" in record.question
        assert record.task_category in {"bugfix", "feature", "injection"}
        assert record.status == "completed" and record.stop_reason == "end_turn"
        assert record.passed and (record.tests_passed, record.tests_total) == (3, 3)
        assert record.files_modified == ["x.py"] and record.detail == "3 passed"
        assert (record.turns, record.input_tokens, record.output_tokens) == (1, 1000, 200)
        assert (record.cache_write_tokens, record.cache_read_tokens) == (0, 300)
        expected = prices().cost(
            config.model,
            TokenUsage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=300),
        )
        assert record.cost_usd == pytest.approx(expected)
        assert record.latency_s >= 0 and record.total_latency_s >= record.latency_s
        assert (record.retries, record.tool_calls, record.tool_errors) == (0, 0, 0)
        assert (record.error, record.error_type, record.error_status) == (None, None, None)
        assert record.git_sha == PROVENANCE.git_sha and record.source == "local"
        assert record.uv_lock_sha256 == PROVENANCE.uv_lock_sha256


def test_the_two_roles_use_different_models_on_the_model_track(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.runner.run(world.plan)
    models = {r.config_role: r.model for r in records_of(world)}
    assert models == {"baseline": "claude-haiku-5-5", "variant": "claude-sonnet-5-5"}
    assert {r["model"] for r in world.client.attempts} == set(models.values())


def test_transcripts_are_written_gzipped_and_redacted(tmp_path: Path) -> None:
    def responder(request: dict[str, Any]) -> Any:
        if is_warmup(request):
            return default_responder(request)
        return text_response("my key is sk-ant-api03-SECRET_value-1", usage())

    world = build_world(tmp_path, responder=responder)
    world.runner.run(world.plan)
    for planned in world.plan.attempts:
        path = world.transcripts_dir / MONDAY / f"{planned.attempt_id}.json.gz"
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            text = handle.read()
        messages = json.loads(text)
        assert messages[0]["role"] == "user" and messages[-1]["role"] == "assistant"
        assert "SECRET_value" not in text and "[REDACTED]" in text


def test_the_integrity_baseline_is_captured_once_per_run(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    captures: list[int] = []
    original = world.integrity.capture
    world.integrity.capture = lambda: (captures.append(1), original())[1]  # type: ignore[method-assign]
    world.runner.run(world.plan)
    assert captures == [1]


def test_the_aa_test_warms_up_once_and_hashes_match(tmp_path: Path) -> None:
    plan = make_plan(SUNDAY, trials=1)
    world = build_world(tmp_path, plan=plan)
    world.runner.run(plan)
    assert len(world.client.warmups) == 1
    records = world.store.read_attempts(SUNDAY)[0]
    assert len({r.config_hash for r in records}) == 1
    assert {r.config_role for r in records} == {"baseline", "variant"}


def test_the_per_attempt_cost_cap_is_applied(tmp_path: Path) -> None:
    def responder(request: dict[str, Any]) -> Any:
        if is_warmup(request):
            return default_responder(request)
        return tool_use_response("t", "list_files", {})  # never finishes: max_turns

    plan = make_plan(trials=1, tasks=["fix-date-range"])
    world = build_world(tmp_path, plan=plan, responder=responder)
    world.runner.run(plan)
    records = records_of(world)
    assert [r.status for r in records] == ["max_turns", "max_turns"]
    assert all(r.turns == 15 and r.tool_calls == 15 for r in records)


# --- budget ----------------------------------------------------------------------------


def test_daily_budget_stops_the_run_without_leaving_half_a_pair(tmp_path: Path) -> None:
    plan = with_caps(make_plan(trials=1), daily=0.07)  # room for one 0.065 pair, not two
    world = build_world(tmp_path, plan=plan)
    outcome = world.runner.run(plan)

    summary = outcome.summary
    assert outcome.kind == "summary" and summary.status == "partial_budget"
    records = records_of(world)
    assert (summary.completed, summary.skipped_budget, summary.skipped_aborted) == (2, 4, 0)
    assert {r.config_role for r in records} == {"baseline", "variant"}  # a whole pair
    assert len({(r.trial, r.task_id) for r in records}) == 1
    skipped = summary.skipped_attempts
    assert len(skipped) == 4 and {s.reason for s in skipped} == {"skipped_budget"}
    ran = {r.attempt_id for r in records}
    assert ran | {s.attempt_id for s in skipped} == {a.attempt_id for a in plan.attempts}
    assert all(s.task_id and s.config_role in {"baseline", "variant"} for s in skipped)
    assert summary.total_cost_usd <= 0.07


def test_the_warmup_cost_counts_against_the_daily_budget(tmp_path: Path) -> None:
    plan = with_caps(make_plan(trials=1), daily=0.002)  # the warm-up alone is ~0.0014
    world = build_world(tmp_path, plan=plan)
    summary = world.runner.run(plan).summary
    assert summary.status == "partial_budget" and summary.completed == 0
    assert summary.skipped_budget == 6
    assert summary.warmup_cost_usd > 0
    assert summary.total_cost_usd == pytest.approx(summary.warmup_cost_usd)
    assert not world.store.has_attempts(MONDAY) and world.store.has_summary(MONDAY)


def test_history_replaces_the_default_estimates(tmp_path: Path) -> None:
    plan = make_plan(trials=1)
    first = build_world(tmp_path / "a", plan=plan)
    first.runner.run(plan)  # a full day: real (tiny) costs for every task and config
    history = first.store.read_all_attempts()[0]

    tight = with_caps(plan, daily=0.07)  # the default estimates would stop this after one pair
    second = build_world(tmp_path / "b", plan=tight, history=history)
    assert second.runner.run(tight).summary.status == "completed"
    unseen = build_world(tmp_path / "c", plan=tight)
    assert unseen.runner.run(tight).summary.status == "partial_budget"


# --- errors ----------------------------------------------------------------------------


def test_an_exception_inside_one_attempt_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    grader = FakeGrader(raise_on_call=2)
    world = build_world(tmp_path, grader=grader)
    outcome = world.runner.run(world.plan)
    records = records_of(world)
    assert outcome.summary.status == "completed" and len(records) == 6
    broken = records[1]
    assert broken.status == "error" and broken.error_type == "RuntimeError"
    assert "sk-ant" not in (broken.error or "") and "[REDACTED]" in (broken.error or "")
    assert (broken.passed, broken.tests_total, broken.cost_usd) == (False, 0, 0.0)
    assert [r.status for r in records].count("completed") == 5
    assert outcome.summary.errored == 1


def test_failed_grades_never_count_as_errors(tmp_path: Path) -> None:
    world = build_world(tmp_path, grader=FakeGrader(passed=False))
    outcome = world.runner.run(world.plan)
    assert outcome.summary.status == "completed" and outcome.summary.errored == 0
    assert not any(r.passed for r in records_of(world))
    assert not world.store.read_aborts()


def test_three_consecutive_errors_abort_the_run_as_an_event(tmp_path: Path) -> None:
    plan = make_plan(trials=2)
    world = build_world(
        tmp_path,
        plan=plan,
        responder=make_responder(lambda n: status_error(400) if 3 <= n <= 5 else None),
    )
    outcome = world.runner.run(plan)

    assert outcome.kind == "abort" and outcome.cause is not None
    assert outcome.cause.reason == "consecutive_errors"
    assert outcome.summary.status == "aborted_error"
    assert not world.store.has_summary(MONDAY)  # the day stays resumable
    assert outcome.path.name.startswith("2026-11-02.abort-")
    abort = world.store.read_aborts(MONDAY)[0]
    assert abort.reason == "consecutive_errors" and abort.abort_count == 1
    assert (abort.error_type, abort.error_status) == ("APIStatusError", 400)
    assert abort.completed == 5 and len(abort.completed_attempt_ids) == 5
    assert abort.skipped_aborted == 7 and abort.skipped_budget == 0
    assert {s.reason for s in abort.skipped_attempts} == {"skipped_aborted"}
    assert len(records_of(world)) == 5


def test_errors_that_are_not_consecutive_do_not_abort(tmp_path: Path) -> None:
    plan = make_plan(trials=2)
    world = build_world(
        tmp_path,
        plan=plan,
        responder=make_responder(lambda n: status_error(400) if n in (2, 3, 5, 6, 8) else None),
    )
    outcome = world.runner.run(plan)
    assert outcome.kind == "summary" and outcome.summary.status == "completed"
    assert outcome.summary.errored == 5


def test_an_auth_error_during_the_attempts_aborts_immediately(tmp_path: Path) -> None:
    world = build_world(
        tmp_path, responder=make_responder(lambda n: auth_error() if n == 2 else None)
    )
    outcome = world.runner.run(world.plan)
    assert outcome.kind == "abort" and outcome.cause is not None
    assert outcome.cause.reason == "auth_error"
    abort = world.store.read_aborts(MONDAY)[0]
    assert (abort.error_type, abort.error_status) == ("AuthenticationError", 401)
    assert abort.completed == 2 and abort.skipped_aborted == 4
    records = records_of(world)
    assert [r.status for r in records] == ["completed", "error"]
    assert (records[1].error_type, records[1].error_status) == ("AuthenticationError", 401)
    assert not world.store.has_summary(MONDAY)


def test_an_auth_error_in_the_warmup_aborts_before_any_attempt(tmp_path: Path) -> None:
    world = build_world(tmp_path, responder=lambda r: auth_error())
    outcome = world.runner.run(world.plan)

    assert outcome.kind == "abort" and outcome.cause is not None
    assert outcome.cause.reason == "warmup_failed"
    assert not world.store.has_attempts(MONDAY) and not world.store.has_summary(MONDAY)
    abort = world.store.read_aborts(MONDAY)[0]
    assert (abort.error_type, abort.error_status) == ("AuthenticationError", 401)
    assert (abort.completed, abort.skipped_aborted) == (0, 6)
    assert [s.attempt_id for s in abort.skipped_attempts] == [
        a.attempt_id for a in world.plan.attempts
    ]
    assert world.client.attempts == []  # not one attempt was sent


def test_other_warmup_errors_are_retried_then_abort(tmp_path: Path) -> None:
    world = build_world(tmp_path, responder=lambda r: status_error(500))
    outcome = world.runner.run(world.plan)
    assert outcome.kind == "abort" and outcome.cause is not None
    assert outcome.cause.error_status == 500
    assert len(world.client.requests) == 3  # RetryPolicy: three tries, then abort
    assert not world.store.has_attempts(MONDAY)


def test_an_unexpected_failure_outside_an_attempt_is_an_abort_that_keeps_earlier_records(
    tmp_path: Path,
) -> None:
    class ExplodingStore(ResultsStore):
        def __init__(self, results_dir: Path) -> None:
            super().__init__(results_dir)
            self.appended = 0

        def append_attempt(self, record: AttemptRecord) -> None:
            if self.appended == 2:
                raise OSError("disk full")
            self.appended += 1
            super().append_attempt(record)

    store = ExplodingStore(tmp_path / "results")
    world = build_world(tmp_path, store=store)
    outcome = world.runner.run(world.plan)
    assert outcome.kind == "abort" and outcome.cause is not None
    assert outcome.cause.reason == "internal_error" and outcome.cause.error_type == "OSError"
    assert len(records_of(world)) == 2  # written immediately: a crash keeps what was done
    assert not world.store.has_summary(MONDAY)


# --- aborts are events: the day resumes -------------------------------------------------


def test_a_warmup_abort_does_not_block_a_later_run_the_same_day(tmp_path: Path) -> None:
    results = tmp_path / "results"
    first = build_world(tmp_path / "one", responder=lambda r: auth_error(), results_dir=results)
    assert first.runner.run(first.plan).kind == "abort"

    second = build_world(tmp_path / "two", results_dir=results)
    outcome = second.runner.run(second.plan)
    assert outcome.kind == "summary" and outcome.summary.status == "completed"
    assert outcome.summary.abort_count == 1 and outcome.summary.completed == 6
    assert second.store.has_summary(MONDAY)
    assert len(second.store.read_aborts(MONDAY)) == 1


def test_a_mid_run_abort_then_resume_completes_the_remaining_attempts(tmp_path: Path) -> None:
    results = tmp_path / "results"
    plan = make_plan(trials=2)
    first = build_world(
        tmp_path / "one",
        plan=plan,
        results_dir=results,
        responder=make_responder(lambda n: status_error(400) if 4 <= n <= 6 else None),
    )
    assert first.runner.run(plan).kind == "abort"
    before = first.store.attempts_path(MONDAY).read_bytes()
    assert len(records_of(first)) == 6

    second = build_world(tmp_path / "two", plan=plan, results_dir=results)
    outcome = second.runner.run(plan)

    assert outcome.kind == "summary" and outcome.summary.status == "completed"
    assert outcome.summary.abort_count == 1
    assert (outcome.summary.planned, outcome.summary.completed) == (12, 12)
    assert outcome.summary.errored == 3
    after = second.store.attempts_path(MONDAY).read_bytes()
    assert after.startswith(before)  # earlier lines untouched, the rest appended
    ids = [r.attempt_id for r in second.store.read_attempts(MONDAY)[0]]
    assert len(ids) == len(set(ids)) == 12
    assert len(second.client.attempts) == 6  # only the remaining attempts were sent
    assert len(second.client.warmups) == 2  # caches are warmed again for the new segment


def test_resuming_a_day_with_nothing_left_still_writes_the_summary(tmp_path: Path) -> None:
    results = tmp_path / "results"
    first = build_world(tmp_path / "one", results_dir=results)
    first.runner.run(first.plan)
    first.store.summary_path(MONDAY).unlink()  # as if the process died before the summary
    second = build_world(tmp_path / "two", results_dir=results)
    outcome = second.runner.run(second.plan)
    assert outcome.summary.status == "completed" and second.client.requests == []


def test_two_aborts_on_the_same_day_make_two_distinct_files(tmp_path: Path) -> None:
    results = tmp_path / "results"
    clock = FixedClock()  # both aborts happen "at the same second"
    for name in ("one", "two"):
        world = build_world(
            tmp_path / name, responder=lambda r: auth_error(), results_dir=results, clock=clock
        )
        assert world.runner.run(world.plan).kind == "abort"
    files = sorted(p.name for p in (results / "runs").glob("*.abort-*.json"))
    assert files == ["2026-11-02.abort-090000.json", "2026-11-02.abort-090001.json"]
    third = build_world(tmp_path / "three", results_dir=results)
    assert third.runner.run(third.plan).summary.abort_count == 2
    stored = [a.abort_count for a in third.store.read_aborts(MONDAY)]
    assert stored == [1, 2]


def test_the_daily_budget_includes_what_earlier_segments_spent(tmp_path: Path) -> None:
    results = tmp_path / "results"
    plan = make_plan(trials=2)
    first = build_world(
        tmp_path / "one",
        plan=plan,
        results_dir=results,
        responder=make_responder(lambda n: status_error(400) if 5 <= n <= 7 else None),
    )
    assert first.runner.run(plan).kind == "abort"
    spent = (
        sum(r.cost_usd for r in records_of(first))
        + first.store.read_aborts(MONDAY)[0].warmup_cost_usd
    )
    assert spent > 0

    tight = with_caps(plan, daily=spent + 0.001)
    second = build_world(tmp_path / "two", plan=tight, results_dir=results)
    outcome = second.runner.run(tight)
    assert outcome.summary.status == "partial_budget"
    assert outcome.summary.skipped_budget == 12 - 7
    assert outcome.summary.abort_count == 1


# --- tampering is final ----------------------------------------------------------------


def test_tampering_writes_the_summary_and_stops_the_run(tmp_path: Path) -> None:
    world = build_world(tmp_path, grader=FakeGrader(tamper_from_call=2))
    outcome = world.runner.run(world.plan)

    assert outcome.kind == "summary" and outcome.summary.status == "aborted_tampered"
    assert world.store.has_summary(MONDAY) and not world.store.read_aborts(MONDAY)
    records = records_of(world)
    assert [r.status for r in records] == ["completed", "environment_tampered"]
    assert records[1].environment_tampered and not records[1].passed
    assert (outcome.summary.completed, outcome.summary.skipped_aborted) == (2, 4)
    assert {s.reason for s in outcome.summary.skipped_attempts} == {"skipped_aborted"}


def test_redaction_reaches_the_records(tmp_path: Path) -> None:
    secret = "plain-secret-value-123"
    grader = FakeGrader(raise_on_call=1)
    world = build_world(tmp_path, grader=grader, redactor=Redactor([secret]))
    world.runner.run(world.plan)
    assert secret not in world.store.attempts_path(MONDAY).read_text()
