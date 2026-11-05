"""Suite and task hashes on records, the resume check, and the calibration overrides."""

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError
from runner_support import (
    MONDAY,
    TASKS_DIR,
    build_cli,
    build_world,
    default_responder,
    is_warmup,
    make_plan,
    tool_use_response,
)

from agent_lab.runner.records import AttemptRecord
from agent_lab.runner.run_command import RunArgs
from agent_lab.runner.storage import ResultsStore

HASH = re.compile(r"[0-9a-f]{12}")


def edited_tasks(tmp_path: Path) -> Path:
    """A copy of the fixture tasks directory that a test may edit."""
    copy = tmp_path / "tasks-copy"
    shutil.copytree(TASKS_DIR, copy)
    return copy


# --- records --------------------------------------------------------------------------------


def test_records_carry_the_suite_hash_the_task_hash_and_the_difficulty(tmp_path: Path) -> None:
    plan = make_plan(trials=1)
    world = build_world(tmp_path, plan=plan)
    world.runner.run(plan)
    records, _ = world.store.read_attempts(plan.date)
    assert len(records) == 6
    suite_hashes = {r.suite_hash for r in records}
    assert len(suite_hashes) == 1 and HASH.fullmatch(next(iter(suite_hashes)) or "")
    per_task = {r.task_id: r.task_hash for r in records}
    assert len(set(per_task.values())) == 3 and all(
        HASH.fullmatch(h or "") for h in per_task.values()
    )
    assert {r.task_difficulty for r in records} == {"easy"}


def test_a_subset_run_has_its_own_suite_hash(tmp_path: Path) -> None:
    full, subset = make_plan(trials=1), make_plan(trials=1, tasks=["fix-date-range"])
    hashes = {}
    for name, plan in (("full", full), ("subset", subset)):
        world = build_world(tmp_path / name, plan=plan)
        world.runner.run(plan)
        hashes[name] = {r.suite_hash for r in world.store.read_attempts(plan.date)[0]}
    assert len(hashes["full"]) == len(hashes["subset"]) == 1
    assert hashes["full"] != hashes["subset"]


def test_editing_a_task_changes_the_suite_hash_of_the_next_run(tmp_path: Path) -> None:
    tasks = edited_tasks(tmp_path)
    seen = []
    for name in ("before", "after"):
        plan = make_plan(trials=1, tasks_dir=tasks)
        world = build_world(tmp_path / name, plan=plan, tasks_dir=tasks)
        world.runner.run(plan)
        seen.append({r.suite_hash for r in world.store.read_attempts(plan.date)[0]})
        (tasks / "fix-date-range" / "project" / "calc.py").write_text(
            "def add(a, b):\n    return 0\n"
        )
    assert seen[0] != seen[1]


def test_old_records_without_the_new_fields_still_parse() -> None:
    line = (
        (Path(__file__).parent / "fixtures" / "attempt_record_v1.jsonl").read_text().splitlines()[0]
    )
    record = AttemptRecord.model_validate_json(line)
    assert record.suite_hash is None and record.task_hash is None and record.task_difficulty is None


def test_a_junk_hash_or_difficulty_makes_the_line_unreadable(tmp_path: Path) -> None:
    plan = make_plan(trials=1, tasks=["fix-date-range"])
    world = build_world(tmp_path, plan=plan)
    world.runner.run(plan)
    good = json.loads(world.store.attempts_path(plan.date).read_text().splitlines()[0])
    for field, junk in (
        ("suite_hash", "../../etc"),
        ("task_hash", "XYZ"),
        ("task_difficulty", "x"),
    ):
        with pytest.raises(ValidationError):
            AttemptRecord.model_validate({**good, field: junk})


def test_the_database_exposes_the_suite_and_difficulty(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    db = tmp_path / "results.db"
    assert (
        world.cli.run(["db", "rebuild", "--db", str(db), "--results-dir", str(world.results_dir)])
        == 0
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT DISTINCT task_difficulty FROM attempts").fetchall() == [
            ("easy",)
        ]
        (suite,) = conn.execute("SELECT DISTINCT suite_hash FROM pairs").fetchone()
        assert HASH.fullmatch(suite)


# --- the command ----------------------------------------------------------------------------


def test_the_dry_run_prints_the_suite_hash(tmp_path: Path) -> None:
    _, output = build_cli(tmp_path, api_key=None).run("--date", MONDAY, "--dry-run")
    assert re.search(r"suite: [0-9a-f]{12} \(3 tasks\)", output)


def test_resume_refuses_when_the_task_suite_changed(tmp_path: Path) -> None:
    tasks = edited_tasks(tmp_path)
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1", "--tasks-dir", str(tasks))
    ResultsStore(world.results_dir).summary_path(MONDAY).unlink()
    (tasks / "fix-csv-total" / "task.yaml").write_text(
        (tasks / "fix-csv-total" / "task.yaml").read_text() + "# edited\n"
    )
    calls = len(world.client.requests)
    code, output = world.run(
        "--date", MONDAY, "--trials", "1", "--resume", "--tasks-dir", str(tasks)
    )
    assert code == 1 and "the task suite changed since the first attempts" in output
    assert len(world.client.requests) == calls


def test_resume_with_the_same_suite_is_accepted(tmp_path: Path) -> None:
    tasks = edited_tasks(tmp_path)
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1", "--tasks-dir", str(tasks))
    ResultsStore(world.results_dir).summary_path(MONDAY).unlink()
    code, _ = world.run("--date", MONDAY, "--trials", "1", "--resume", "--tasks-dir", str(tasks))
    assert code == 0


def test_resume_refuses_a_day_recorded_before_suites_were_versioned(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    store = ResultsStore(world.results_dir)
    store.summary_path(MONDAY).unlink()
    path = store.attempts_path(MONDAY)
    old = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        for field in ("suite_hash", "task_hash", "task_difficulty"):
            row.pop(field)
        old.append(json.dumps(row))
    path.write_text("\n".join(old) + "\n")
    code, output = world.run("--date", MONDAY, "--trials", "1", "--resume")
    assert code == 1 and "recorded none" in output


# --- calibration overrides (scripts/calibrate.py) -----------------------------------------


def calibration_args(world, **overrides) -> RunArgs:  # type: ignore[no-untyped-def]
    values = {
        "date": MONDAY,
        "track": "aa-test",
        "trials": 2,
        "results_dir": world.results_dir,
        "transcripts_dir": world.tmp_path / "transcripts",
        "tasks_dir": TASKS_DIR,
        "schedule": Path(__file__).parents[2] / "experiments" / "schedule.yaml",
        "baseline_only": True,
    }
    return RunArgs(**{**values, **overrides})


def test_baseline_only_runs_just_the_baseline_side(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    assert world.command.execute(calibration_args(world)) == 0
    records, _ = ResultsStore(world.results_dir).read_all_attempts()
    assert len(records) == 6 and {r.config_role for r in records} == {"baseline"}


def test_the_model_override_changes_the_config_and_its_hash(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.command.execute(calibration_args(world))
    world.command.execute(
        calibration_args(world, date="2026-11-03", model="claude-sonnet-5-5", trials=1)
    )
    records, _ = ResultsStore(world.results_dir).read_all_attempts()
    by_model = {r.model: r.config_hash for r in records}
    assert set(by_model) == {"claude-haiku-5-5", "claude-sonnet-5-5"}
    assert len(set(by_model.values())) == 2


def test_an_unknown_model_is_refused_before_any_api_call(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    assert world.command.execute(calibration_args(world, model="gpt-9")) == 1
    assert world.client.requests == []
    assert "no pricing for model 'gpt-9'" in world.out.getvalue()


def test_the_max_cost_override_stops_the_run(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.command.execute(calibration_args(world, max_cost_usd=0.0001, trials=4))
    summary = ResultsStore(world.results_dir).read_summaries()[0][0]
    assert summary.status == "partial_budget" and summary.skipped_budget > 0


def test_without_overrides_a_plan_is_unchanged(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    assert world.command.execute(calibration_args(world, baseline_only=False, trials=1)) == 0
    records, _ = ResultsStore(world.results_dir).read_all_attempts()
    assert {r.config_role for r in records} == {"baseline", "variant"}


def test_the_attempt_cap_override_applies(tmp_path: Path) -> None:
    def keeps_calling_tools(request):  # type: ignore[no-untyped-def]
        if is_warmup(request):
            return default_responder(request)
        return tool_use_response("t1", "list_files", {})

    world = build_cli(tmp_path, responder=keeps_calling_tools)
    world.command.execute(calibration_args(world, max_attempt_cost_usd=0.000001, trials=1))
    records, _ = ResultsStore(world.results_dir).read_all_attempts()
    assert records and {r.status for r in records} == {"budget_exceeded"}
