"""Builders for the report tests: real result files from the real writers, plus data models."""

import shutil
from pathlib import Path
from typing import Any

from runner_support import (
    auth_error,
    build_world,
    default_responder,
    is_warmup,
    make_plan,
    status_error,
)

from agent_lab.report.model import Delta, Exclusions, SeriesReport, SideStats
from agent_lab.runner.plan import RunPlan
from agent_lab.runner.storage import ResultsStore

MON, TUE, WED, SUN, NEXT_TUE = "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-08", "2026-11-10"


def run_day(
    tmp: Path,
    results: Path,
    day: str,
    *,
    trials: int = 4,
    plan: RunPlan | None = None,
    **kwargs: Any,
) -> RunPlan:
    """Run one fake day with the real runner and writers into ``results``."""
    plan = plan or make_plan(day, trials=trials)
    world = build_world(tmp, plan=plan, results_dir=results, **kwargs)
    world.runner.run(plan)
    return plan


def error_on(*numbers: int):  # type: ignore[no-untyped-def]
    """A responder whose n-th attempt (1-based) fails with a non-transient API error."""
    state = {"n": 0}

    def responder(request: dict[str, Any]) -> Any:
        if is_warmup(request):
            return default_responder(request)
        state["n"] += 1
        return status_error(400) if state["n"] in numbers else default_responder(request)

    return responder


def warmup_fails(request: dict[str, Any]) -> Any:
    return auth_error() if is_warmup(request) else default_responder(request)


def changed_variant(plan: RunPlan, **changes: Any) -> RunPlan:
    """The same plan with the variant config edited, as if schedule.yaml had changed."""
    resolved = plan.resolved
    variant = resolved.variant.model_copy(update=changes)
    return plan.model_copy(update={"resolved": resolved.model_copy(update={"variant": variant})})


def realistic(tmp: Path) -> Path:
    """Several days and tracks written by the real runner: a results directory.

    Monday model-comparison with one errored pair, Tuesday tool-design (later redone with an
    edited variant), Wednesday aborted at the warm-up, Sunday aa-test.
    """
    results = tmp / "results"
    run_day(tmp / "mon", results, MON, responder=error_on(3))
    run_day(tmp / "tue", results, TUE)
    run_day(tmp / "wed", results, WED, responder=warmup_fails)
    run_day(tmp / "sun", results, SUN)
    edited = changed_variant(make_plan(NEXT_TUE, trials=4), max_turns=9)
    run_day(tmp / "tue2", results, NEXT_TUE, plan=edited)
    return results


def copy_results(source: Path, dest: Path) -> Path:
    shutil.copytree(source, dest)
    return dest


def store(results: Path) -> ResultsStore:
    return ResultsStore(results)


def side(rate: float = 0.5, n: int = 12, ci: bool = True) -> SideStats:
    return SideStats(
        n=n,
        passed=round(rate * n),
        rate=rate,
        ci_low=max(0.0, rate - 0.2) if ci else None,
        ci_high=min(1.0, rate + 0.2) if ci else None,
        mean_cost_usd=0.003,
        cost_per_solved_usd=0.006,
    )


def delta(
    mean: float = 0.0, half: float | None = 1.0, verdict: str = "no detectable difference"
) -> Delta:
    return Delta(
        mean=mean,
        low=None if half is None else mean - half,
        high=None if half is None else mean + half,
        verdict=verdict,
    )


def series(**overrides: Any) -> SeriesReport:
    values: dict[str, Any] = {
        "track": "tool-design",
        "question": "Do the structured tools beat a single bash tool?",
        "baseline_hash": "a" * 12,
        "variant_hash": "b" * 12,
        "current": True,
        "first_date": "2026-11-03",
        "last_date": "2026-11-10",
        "days": 2,
        "pairs": 24,
        "tasks": 3,
        "excluded": Exclusions(),
        "baseline": side(0.5),
        "variant": side(0.75),
        "delta_pass_pp": delta(25.0, 20.0, "variant higher"),
        "delta_cost_usd": delta(0.001, 0.0005, "variant higher"),
        "delta_turns": delta(1.0, 0.5, "variant higher"),
        "delta_output_tokens": delta(10.0, 100.0),
        "ceiling": False,
        "noise_floor": False,
        "method": "pass rate: Newcombe; others: bootstrap",
        "description": "tool_set: structured → bash_only",
    }
    return SeriesReport(**{**values, **overrides})
