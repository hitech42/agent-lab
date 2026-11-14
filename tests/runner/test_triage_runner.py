"""Triage inside the experiment runner: labels in the record, accounting, budget, resilience."""

from pathlib import Path
from typing import Any

import pytest
from runner_support import (
    SUNDAY,
    FakeGrader,
    build_world,
    make_plan,
    text_response,
    usage,
    with_caps,
)
from triage_support import model_triage, verdict

from agent_lab.benchmark.grader import GradeResult
from agent_lab.harness.isolation import NoIsolation
from agent_lab.runner.baseline import BaselineProbe
from agent_lab.runner.triage import FailureTriage
from agent_lab.runner.triage_rules import RuleVerdict, TriageRules


class ModelOnlyRules(TriageRules):
    """No rule ever matches, so every failure goes to the (fake) model."""

    def classify(self, *args: Any, **kwargs: Any) -> RuleVerdict | None:
        return None


def modelled(replies: Any) -> tuple[FailureTriage, Any]:
    model, client = model_triage(replies)
    return FailureTriage(ModelOnlyRules(), model), client


def run(tmp_path: Path, triage: FailureTriage, grader: FakeGrader | None = None, **kw: Any):  # type: ignore[no-untyped-def]
    plan = kw.pop("plan", None) or make_plan(SUNDAY, trials=1)
    world = build_world(
        tmp_path, plan=plan, grader=grader or FakeGrader(passed=False), triage=triage, **kw
    )
    outcome = world.runner.run(plan)
    records, _ = world.store.read_attempts(plan.date)
    return world, outcome, records


def test_a_rule_failure_is_labelled_in_the_appended_record(tmp_path: Path) -> None:
    _, outcome, records = run(tmp_path, FailureTriage(TriageRules()))
    assert outcome.summary.status == "completed" and records
    assert {(r.triage_label, r.triage_source) for r in records} == {("no_verification", "rule")}
    assert all(r.triage_cost_usd == 0.0 and r.triage_note for r in records)


def test_a_model_failure_carries_the_models_label_and_cost(tmp_path: Path) -> None:
    replies = [verdict("incomplete_fix", "missed an edge case")] * 40
    triage, client = modelled(replies)
    _, _, records = run(tmp_path, triage)
    assert {(r.triage_label, r.triage_source) for r in records} == {("incomplete_fix", "model")}
    assert all(r.triage_cost_usd > 0 and r.triage_tokens == 2300 for r in records)
    assert len(client.requests) == len(records)


def test_a_passed_attempt_is_not_labelled_or_charged(tmp_path: Path) -> None:
    triage, client = modelled([verdict("other")] * 40)
    _, _, records = run(tmp_path, triage, FakeGrader(passed=True))
    assert all(r.triage_label is None and r.triage_cost_usd == 0.0 for r in records)
    assert client.requests == []


def test_triage_cost_is_kept_apart_from_the_attempt_cost_and_turns(tmp_path: Path) -> None:
    base_triage = FailureTriage(TriageRules())
    _, _, plain = run(tmp_path / "a", base_triage)
    triage, _ = modelled([verdict("wrong_approach")] * 40)
    _, outcome, records = run(tmp_path / "b", triage)
    assert [(r.cost_usd, r.turns, r.input_tokens, r.output_tokens) for r in records] == [
        (r.cost_usd, r.turns, r.input_tokens, r.output_tokens) for r in plain
    ]
    summary = outcome.summary
    triage_total = sum(r.triage_cost_usd for r in records)
    assert summary.triage_cost_usd == pytest.approx(triage_total) and triage_total > 0
    assert summary.triage_calls == len(records)
    expected = sum(r.cost_usd for r in records) + summary.warmup_cost_usd + triage_total
    assert summary.total_cost_usd == pytest.approx(expected)


def test_the_daily_cap_counts_triage_in_the_pair_reservation(tmp_path: Path) -> None:
    plan = with_caps(make_plan(SUNDAY, trials=1), daily=0.009)
    # Two haiku attempts are estimated at $0.008; the warm-up and two triage reserves push the
    # pair over the cap, while without triage it would fit.
    triage, _ = modelled([verdict("other")] * 40)
    _, outcome, records = run(tmp_path / "with", triage, plan=plan)
    assert outcome.summary.status == "partial_budget" and records == []
    _, outcome, records = run(tmp_path / "without", FailureTriage(TriageRules()), plan=plan)
    assert records and outcome.summary.completed == len(records)


def test_a_resumed_day_starts_from_the_triage_spend_already_made(tmp_path: Path) -> None:
    triage, _ = modelled([verdict("other")] * 80)
    plan = make_plan(SUNDAY, trials=1, tasks=["add-top-flag"])
    world, first, records = run(tmp_path, triage, plan=plan)
    (world.store.summary_path(plan.date)).unlink()  # the day becomes resumable
    spent = sum(r.cost_usd + r.triage_cost_usd for r in records) + first.summary.warmup_cost_usd
    full = make_plan(SUNDAY, trials=1, tasks=["add-top-flag", "fix-csv-total"])
    need = 0.008 + 2 * triage.reserve_usd
    capped = with_caps(full, daily=spent + need - 1e-5)  # fits only if triage spend is ignored
    again = build_world(
        tmp_path, plan=capped, grader=FakeGrader(passed=False), triage=triage, store=world.store
    )
    outcome = again.runner.run(capped)
    assert outcome.summary.status == "partial_budget"


def test_a_model_call_that_the_budget_cannot_afford_is_skipped(tmp_path: Path) -> None:
    def pricey(request: dict[str, Any]) -> Any:
        if request["max_tokens"] == 1:
            return text_response("ok", usage(i=10, o=1))
        return text_response("done", usage(i=1_000_000, o=1))  # $0.10 an attempt

    triage, client = modelled([verdict("other")] * 40)
    plan = with_caps(make_plan(SUNDAY, trials=1), daily=0.2)
    _, _, records = run(tmp_path, triage, plan=plan, responder=pricey)
    assert len(records) == 2 and len(client.requests) == 1
    assert [r.triage_source for r in records] == ["model", "error"]
    assert "budget" in (records[1].triage_note or "") and records[1].triage_cost_usd == 0.0


def test_a_triage_api_error_never_fails_or_aborts_the_run(tmp_path: Path) -> None:
    from runner_support import auth_error

    triage, _ = modelled(auth_error())
    _, outcome, records = run(tmp_path, triage)
    assert outcome.kind == "summary" and outcome.summary.status == "completed"
    assert outcome.summary.errored == 0
    assert {(r.status, r.triage_label, r.triage_source) for r in records} == {
        ("completed", "other", "error")
    }
    assert all(r.triage_cost_usd == 0.0 for r in records)


def test_a_bug_inside_triage_is_contained(tmp_path: Path) -> None:
    class Exploding(FailureTriage):
        def triage(self, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            raise RuntimeError("bug sk-ant-secret")

    _, outcome, records = run(tmp_path, Exploding(TriageRules()))
    assert outcome.summary.status == "completed"
    assert {(r.status, r.triage_label, r.triage_source) for r in records} == {
        ("completed", "other", "error")
    }
    assert all("sk-ant" not in (r.triage_note or "") for r in records)


def test_an_exception_attempt_gets_an_infrastructure_error_label(tmp_path: Path) -> None:
    _, _, records = run(
        tmp_path, FailureTriage(TriageRules()), FakeGrader(raise_on_call=1, passed=False)
    )
    errored = [r for r in records if r.status == "error"]
    assert errored and {r.triage_label for r in errored} == {"infrastructure_error"}


def test_regression_is_labelled_from_the_pre_run(tmp_path: Path) -> None:
    class PassingGrader:
        def grade(self, task, workspace, sandbox, final_text):  # type: ignore[no-untyped-def]
            return _grade(passed_tests=["m::old"], failed_tests=[])

    class Attempts(FakeGrader):
        def grade(self, task, workspace, sandbox, final_text):  # type: ignore[no-untyped-def]
            base = super().grade(task, workspace, sandbox, final_text)
            return base.model_copy(update={"failed_tests": ["m::old", "m::new"]})

    probe = BaselineProbe(lambda task: PassingGrader(), NoIsolation())  # type: ignore[arg-type,return-value]
    _, _, records = run(
        tmp_path, FailureTriage(TriageRules()), Attempts(passed=False), baselines=probe
    )
    assert {r.triage_label for r in records} == {"regression"}
    assert all("m::old" in (r.triage_note or "") for r in records)


def test_an_unavailable_pre_run_is_noted_and_the_run_completes(tmp_path: Path) -> None:
    class Failing:
        def grade(self, task, workspace, sandbox, final_text) -> GradeResult:  # type: ignore[no-untyped-def]
            raise RuntimeError("probe broke")

    probe = BaselineProbe(lambda task: Failing(), NoIsolation())  # type: ignore[arg-type,return-value]
    _, outcome, records = run(tmp_path, FailureTriage(TriageRules()), baselines=probe)
    assert outcome.summary.status == "completed"
    assert all(
        "regression check unavailable: RuntimeError" in (r.triage_note or "") for r in records
    )


def _grade(**kw: Any) -> GradeResult:
    from triage_support import grade

    return grade(**kw)
