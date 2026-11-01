"""End to end with the REAL grader: a scripted model that fixes fix-date-range via its tools."""

from pathlib import Path
from typing import Any

import pytest
from runner_support import (
    TASKS_DIR,
    build_world,
    default_responder,
    is_warmup,
    make_plan,
    text_response,
    tool_use_response,
    usage,
)

from agent_lab.benchmark.grader import Grader

pytestmark = pytest.mark.slow  # real grading spawns pytest subprocesses

SOLUTION = (TASKS_DIR / "fix-date-range" / "solution" / "daterange.py").read_text()
TUESDAY = "2026-11-03"  # tool-design: baseline uses the structured tools, variant only bash


def solver(on_first_request: Any = None) -> Any:
    """Writes the reference solution with whichever tool set the request offers, then stops."""
    state = {"first_requests": 0}

    def responder(request: dict[str, Any]) -> Any:
        if is_warmup(request):
            return default_responder(request)
        if len(request["messages"]) > 1:
            return text_response("fixed", usage(i=2000, o=100))
        state["first_requests"] += 1
        if on_first_request:
            on_first_request(state["first_requests"])
        names = {tool["name"] for tool in request["tools"]}
        if names == {"bash"}:
            command = f"python - <<'PY'\nopen('daterange.py', 'w').write({SOLUTION!r})\nPY"
            return tool_use_response("t1", "bash", {"command": command})
        return tool_use_response("t1", "create_file", {"path": "daterange.py", "content": SOLUTION})

    return responder


def test_a_day_with_real_grading(tmp_path: Path) -> None:
    plan = make_plan(TUESDAY, trials=1, tasks=["fix-date-range"])
    world = build_world(
        tmp_path,
        plan=plan,
        responder=solver(),
        grader_factory=lambda task, integrity: Grader.for_task(task, integrity),
    )
    outcome = world.runner.run(plan)

    assert outcome.summary.status == "completed" and outcome.summary.completed == 2
    records = world.store.read_attempts(TUESDAY)[0]
    assert {r.tool_set for r in records} == {"structured", "bash_only"}
    for record in records:
        assert record.status == "completed" and record.passed
        assert record.tests_total == 20 and record.tests_passed == 20
        assert record.files_modified == ["daterange.py"] and record.files_added == []
        assert record.protected_violations == [] and record.canaries_triggered == []
        assert (record.turns, record.tool_calls, record.tool_errors) == (2, 1, 0)
        assert record.cost_usd > 0 and record.environment_tampered is False
    assert len(world.client.warmups) == 2  # one per distinct config (different tool sets)


def test_unfixed_attempts_fail_the_real_hidden_tests(tmp_path: Path) -> None:
    plan = make_plan(TUESDAY, trials=1, tasks=["fix-date-range"])
    world = build_world(
        tmp_path,
        plan=plan,
        responder=default_responder,  # answers immediately without touching the project
        grader_factory=lambda task, integrity: Grader.for_task(task, integrity),
    )
    world.runner.run(plan)
    for record in world.store.read_attempts(TUESDAY)[0]:
        assert record.status == "completed" and not record.passed
        assert record.tests_failed > 0 and record.files_modified == []


def test_tampering_with_site_packages_is_final_and_recorded(tmp_path: Path) -> None:
    plan = make_plan(TUESDAY, trials=1, tasks=["fix-date-range"])
    holder: dict[str, Path] = {}

    def poison(first_request_number: int) -> None:
        if first_request_number == 2:
            (holder["site"] / "evil.pth").write_text("import os\n")

    world = build_world(
        tmp_path,
        plan=plan,
        responder=solver(poison),
        grader_factory=lambda task, integrity: Grader.for_task(task, integrity),
    )
    holder["site"] = world.site_dir
    outcome = world.runner.run(plan)

    assert outcome.kind == "summary" and outcome.summary.status == "aborted_tampered"
    first, second = world.store.read_attempts(TUESDAY)[0]
    assert first.status == "completed" and first.passed
    assert second.status == "environment_tampered" and not second.passed
    assert "evil.pth" in second.detail and second.environment_tampered
    assert world.store.has_summary(TUESDAY)
