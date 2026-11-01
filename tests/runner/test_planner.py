from datetime import date

import pytest
from runner_support import MONDAY, SUNDAY, make_plan, schedule, suite

from agent_lab.benchmark.loader import UnknownTaskError
from agent_lab.runner.plan import PlanOptions, RunPlanner
from agent_lab.runner.schedule import ScheduleError

TASKS = ["add-top-flag", "fix-csv-total", "fix-date-range"]


def test_plan_has_every_trial_task_and_role() -> None:
    plan = make_plan(MONDAY)
    assert len(plan.attempts) == 4 * len(TASKS) * 2  # 4 trials from the schedule
    assert {(a.trial, a.task_id, a.config_role) for a in plan.attempts} == {
        (t, task, role) for t in range(1, 5) for task in TASKS for role in ("baseline", "variant")
    }
    assert [a.order for a in plan.attempts] == list(range(1, 25))
    assert len({a.attempt_id for a in plan.attempts}) == 24
    assert plan.attempts[0].attempt_id.startswith("2026-11-02-")


def test_both_configs_run_back_to_back_for_each_trial_and_task() -> None:
    plan = make_plan(MONDAY)
    pairs = [(plan.attempts[i], plan.attempts[i + 1]) for i in range(0, len(plan.attempts), 2)]
    for first, second in pairs:
        assert (first.trial, first.task_id) == (second.trial, second.task_id)
        assert {first.config_role, second.config_role} == {"baseline", "variant"}


def test_trials_are_the_outer_loop_and_tasks_are_sorted() -> None:
    plan = make_plan(MONDAY)
    order = [(a.trial, a.task_id) for a in plan.attempts[::2]]
    assert order == [(t, task) for t in range(1, 5) for task in TASKS]


def test_the_same_date_gives_the_same_plan() -> None:
    assert make_plan(MONDAY) == make_plan(MONDAY)
    assert make_plan(MONDAY).model_dump() == make_plan(MONDAY).model_dump()


def test_pair_order_is_shuffled_and_differs_between_dates() -> None:
    def first_roles(day: str) -> list[str]:
        return [a.config_role for a in make_plan(day).attempts[::2]]

    roles = first_roles(MONDAY)
    assert "baseline" in roles and "variant" in roles  # not always the same one first
    assert roles != first_roles("2026-11-09")  # a different date shuffles differently


def test_options_trials_tasks_and_track() -> None:
    plan = make_plan(MONDAY, trials=1, tasks=["fix-date-range"])
    assert {a.task_id for a in plan.attempts} == {"fix-date-range"}
    assert len(plan.attempts) == 2 and plan.resolved.trials_per_task == 1
    named = make_plan(MONDAY, track="aa-test", trials=1)
    assert named.resolved.track.name == "aa-test" and len(named.attempts) == 6


def test_unknown_task_and_track() -> None:
    planner = RunPlanner(schedule(), suite())
    with pytest.raises(UnknownTaskError):
        planner.plan(date(2026, 11, 2), PlanOptions(tasks=["nope"]))
    with pytest.raises(ScheduleError):
        planner.plan(date(2026, 11, 2), PlanOptions(track="nope"))


def test_plan_carries_the_resolved_track() -> None:
    plan = make_plan(SUNDAY)
    assert plan.date == SUNDAY and plan.resolved.track.name == "aa-test"
