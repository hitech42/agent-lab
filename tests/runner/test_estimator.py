from runner_support import MONDAY, PROVENANCE, SUNDAY, make_plan, prices, schedule

from agent_lab.benchmark.grader import GradeResult
from agent_lab.benchmark.manifest import ChangedFiles
from agent_lab.harness.agent import AttemptResult
from agent_lab.harness.redact import Redactor
from agent_lab.harness.tracing import AttemptTrace
from agent_lab.runner.estimator import CostEstimator
from agent_lab.runner.plan import RunPlan
from agent_lab.runner.records import AttemptRecord, RecordBuilder
from agent_lab.runner.schedule import ExperimentSchedule


def record(plan: RunPlan, role: str, task_id: str, cost: float) -> AttemptRecord:
    config = plan.resolved.baseline if role == "baseline" else plan.resolved.variant
    planned = next(a for a in plan.attempts if a.config_role == role and a.task_id == task_id)
    builder = RecordBuilder("r", plan.date, "t", "q", PROVENANCE, Redactor([]))
    result = AttemptResult(
        status="completed",
        stop_reason="end_turn",
        final_text="",
        trace=AttemptTrace(),
        cost_usd=cost,
    )
    grade = GradeResult(
        passed=True,
        tests_passed=1,
        tests_failed=0,
        tests_errors=0,
        tests_total=1,
        protected_violations=[],
        canaries_triggered=[],
        injection_resisted=None,
        changed_files=ChangedFiles(),
        detail="",
    )
    return builder.build(
        planned,
        task_id,
        "bugfix",
        config.name,
        ExperimentSchedule.config_hash(config),
        config.model,
        config.tool_set,
        result=result,
        grade=grade,
    )


def estimator(history) -> CostEstimator:  # type: ignore[no-untyped-def]
    return CostEstimator(history, schedule().cost_defaults, prices())


def test_without_history_the_per_model_defaults_apply() -> None:
    plan = make_plan(MONDAY, trials=1)
    est = estimator([])
    haiku = est.attempt_cost(plan.resolved.baseline, "fix-date-range")
    sonnet = est.attempt_cost(plan.resolved.variant, "fix-date-range")
    assert (haiku, sonnet) == (0.005, 0.06)
    assert est.plan_cost(plan) == 3 * (0.005 + 0.06)


def test_history_for_the_same_config_and_task_wins() -> None:
    plan = make_plan(MONDAY, trials=1)
    history = [
        record(plan, "baseline", "fix-date-range", 0.010),
        record(plan, "baseline", "fix-date-range", 0.020),
        record(plan, "baseline", "fix-csv-total", 0.5),
    ]
    est = estimator(history)
    assert est.attempt_cost(plan.resolved.baseline, "fix-date-range") == 0.015
    assert est.attempt_cost(plan.resolved.baseline, "fix-csv-total") == 0.5
    assert est.attempt_cost(plan.resolved.baseline, "add-top-flag") == 0.005  # no history


def test_model_history_is_used_when_the_config_hash_is_new() -> None:
    plan = make_plan(MONDAY, trials=1)
    history = [record(plan, "baseline", "fix-date-range", 0.04)]
    other = plan.resolved.baseline.model_copy(update={"max_turns": 7})  # new hash, same model
    est = estimator(history)
    assert est.attempt_cost(other, "fix-date-range") == 0.04
    assert est.attempt_cost(plan.resolved.variant, "fix-date-range") == 0.06  # sonnet: default


def test_zero_cost_history_is_ignored() -> None:
    plan = make_plan(MONDAY, trials=1)
    history = [record(plan, "baseline", "fix-date-range", 0.0)]
    assert estimator(history).attempt_cost(plan.resolved.baseline, "fix-date-range") == 0.005


def test_aa_test_estimates_use_the_shared_hash() -> None:
    plan = make_plan(SUNDAY, trials=1)
    history = [record(plan, "baseline", "fix-date-range", 0.03)]
    est = estimator(history)
    assert est.attempt_cost(plan.resolved.variant, "fix-date-range") == 0.03


def test_warmup_estimate_is_small_positive_and_deduplicated_for_the_aa_test() -> None:
    est = estimator([])
    monday = make_plan(MONDAY, trials=1)
    sunday = make_plan(SUNDAY, trials=1)
    one = est.warmup_cost(sunday.resolved.baseline)
    assert 0 < one < 0.01
    assert est.warmup_cost_for_plan(sunday) == one  # identical configs warm once
    assert est.warmup_cost_for_plan(monday) > one  # two models warm twice
