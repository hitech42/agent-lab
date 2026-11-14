from pathlib import Path

import pytest
from runner_support import REAL_TASKS_DIR, FakeGrader, build_world, make_plan, suite
from triage_support import grade

from agent_lab.benchmark.grader import Grader, GradeResult
from agent_lab.benchmark.task import Task
from agent_lab.harness.isolation import NoIsolation
from agent_lab.runner.baseline import BaselineProbe


class ScriptedGrader:
    def __init__(self, outcome: GradeResult | Exception) -> None:
        self.outcome = outcome
        self.calls = 0

    def grade(self, task: Task, workspace, sandbox, final_text: str) -> GradeResult:  # type: ignore[no-untyped-def]
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def probe_with(outcome: GradeResult | Exception) -> tuple[BaselineProbe, ScriptedGrader]:
    grader = ScriptedGrader(outcome)
    return BaselineProbe(lambda task: grader, NoIsolation()), grader  # type: ignore[arg-type,return-value]


def a_task() -> Task:
    return suite().get("fix-csv-total")


def test_the_probe_records_the_hidden_tests_that_pass_untouched() -> None:
    probe, _ = probe_with(grade(passed_tests=["m::a", "m::b"], failed_tests=["m::c"]))
    baseline = probe.probe(a_task(), "a" * 12)
    assert baseline.passing == {"m::a", "m::b"} and baseline.unavailable is None
    assert baseline.task_hash == "a" * 12


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (RuntimeError("boom sk-ant-secret"), "RuntimeError"),
        (grade(tampered=True, passed_tests=["m::a"]), "environment changed"),
        (grade(), "no tests collected"),
    ],
)
def test_a_probe_that_cannot_tell_is_unavailable_and_never_raises(
    outcome: GradeResult | Exception, reason: str
) -> None:
    probe, _ = probe_with(outcome)
    baseline = probe.probe(a_task(), "a" * 12)
    assert baseline.unavailable == reason and baseline.passing == frozenset()
    assert "sk-ant" not in (baseline.unavailable or "")


def test_every_task_is_probed_once_per_run_however_many_trials_and_roles(tmp_path: Path) -> None:
    probing = ScriptedGrader(grade(passed_tests=["m::a"], failed_tests=["m::b"]))
    probe = BaselineProbe(lambda task: probing, NoIsolation())  # type: ignore[arg-type,return-value]
    plan = make_plan(trials=2)
    world = build_world(tmp_path, plan=plan, baselines=probe)
    world.runner.run(plan)
    tasks = {a.task_id for a in plan.attempts}
    assert probing.calls == len(tasks)
    assert world.grader.calls == len(plan.attempts)  # attempts are graded by their own grader


def test_exact_answer_tasks_are_not_probed(tmp_path: Path) -> None:
    class ExactAnswerSuite:
        def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
            self._inner = inner

        def get(self, task_id: str) -> Task:
            return self._inner.get(task_id).model_copy(update={"grading": "exact_answer"})

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self._inner, name)

    probing = ScriptedGrader(grade(passed_tests=["m::a"], failed_tests=["m::b"]))
    probe = BaselineProbe(lambda task: probing, NoIsolation())  # type: ignore[arg-type,return-value]
    plan = make_plan(trials=1)
    world = build_world(tmp_path, plan=plan, baselines=probe)
    world.runner._suite = ExactAnswerSuite(world.runner._suite)  # type: ignore[assignment]
    world.runner.run(plan)
    assert probing.calls == 0


def test_a_probe_that_fails_does_not_fail_the_run(tmp_path: Path) -> None:
    probe, probing = probe_with(RuntimeError("probe exploded"))
    plan = make_plan(trials=1)
    world = build_world(tmp_path, plan=plan, grader=FakeGrader(passed=False), baselines=probe)
    outcome = world.runner.run(plan)
    assert outcome.kind == "summary" and outcome.summary.status == "completed"
    records, _ = world.store.read_attempts(plan.date)
    assert len(records) == len(plan.attempts) and probing.calls > 0
    assert all(r.status == "completed" for r in records)


@pytest.mark.slow
def test_the_real_probe_finds_the_hidden_tests_that_already_pass() -> None:
    task = suite(REAL_TASKS_DIR).get("fix-settings-merge")
    probe = BaselineProbe(lambda t: Grader.for_task(t), NoIsolation())
    baseline = probe.probe(task, "b" * 12)
    assert baseline.unavailable is None and baseline.passing
    assert not any(test.startswith("grade_") for test in baseline.passing)
    solved = BaselineProbe(lambda t: Grader.for_task(t), NoIsolation())
    again = solved.probe(task, "b" * 12)
    assert again.passing == baseline.passing  # random hidden directory names do not matter
