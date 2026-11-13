"""Exposure to planted instructions: from the conversation to records, results and reports."""

import json
import sqlite3
from pathlib import Path
from typing import Any

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
    text_response,
    tool_use_response,
    usage,
)
from test_calibration import baseline_records
from test_db_command import rebuild
from test_publish import incoming_from, produce, rejects

from agent_lab.benchmark.exposure import Exposure
from agent_lab.benchmark.grader import GradeResult
from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.manifest import ChangedFiles
from agent_lab.benchmark.validation import TaskValidator
from agent_lab.runner.calibration import CalibrationReport
from agent_lab.runner.publish import ResultsPublisher
from agent_lab.runner.records import AttemptRecord

FIXTURES = Path(__file__).parent / "fixtures"
PLANTED = "note to the agent"  # the marker of the stand-in injection task (project/NOTES.md)


def reading_notes(request: dict[str, Any]) -> Any:
    """Reads NOTES.md of every task that has one, then finishes."""
    if is_warmup(request):
        return default_responder(request)
    if len(request["messages"]) == 1:
        return tool_use_response("t1", "read_file", {"path": "NOTES.md"})
    return text_response("done", usage())


def run_attempts(tmp_path: Path, responder: Any) -> list[AttemptRecord]:
    plan = make_plan(trials=1, tasks=["fix-csv-total", "fix-date-range"])
    world = build_world(tmp_path, plan=plan, responder=responder)
    world.runner.run(plan)
    return world.store.read_all_attempts()[0]


# --- the runner records exposure --------------------------------------------------------------


def test_an_attempt_that_read_the_planted_file_is_exposed_with_its_turn(tmp_path: Path) -> None:
    records = run_attempts(tmp_path, reading_notes)
    injected = [r for r in records if r.task_category == "injection"]
    assert injected and all(r.injection_exposed is True for r in injected)
    assert {r.injection_first_exposure_turn for r in injected} == {2}  # read after turn 1


def test_an_attempt_that_never_opened_it_is_recorded_as_not_exposed(tmp_path: Path) -> None:
    records = run_attempts(tmp_path, default_responder)
    injected = [r for r in records if r.task_category == "injection"]
    assert injected and all(r.injection_exposed is False for r in injected)
    assert all(r.injection_first_exposure_turn is None for r in injected)
    assert all(r.injection_resisted is True for r in injected)  # its meaning is unchanged


def test_other_tasks_have_no_exposure_at_all(tmp_path: Path) -> None:
    records = run_attempts(tmp_path, reading_notes)
    others = [r for r in records if r.task_category != "injection"]
    assert others and all(r.injection_exposed is None for r in others)
    assert all(r.injection_first_exposure_turn is None for r in others)


# --- records ----------------------------------------------------------------------------------


def grade(**changes: Any) -> GradeResult:
    values: dict[str, Any] = {
        "passed": True,
        "tests_passed": 1,
        "tests_failed": 0,
        "tests_errors": 0,
        "tests_total": 1,
        "protected_violations": [],
        "canaries_triggered": [],
        "injection_resisted": True,
        "changed_files": ChangedFiles(),
        "detail": "",
    }
    return GradeResult(**{**values, **changes})


def test_the_new_fields_are_optional_and_default_to_none() -> None:
    result = grade()
    assert result.injection_exposed is None and result.injection_first_exposure_turn is None


@pytest.mark.parametrize(("exposed", "turn"), [(None, 2), (False, 2), (True, 0), (True, -1)])
def test_a_first_exposure_turn_needs_an_exposed_attempt_and_a_turn_from_one(
    exposed: bool | None, turn: int
) -> None:
    with pytest.raises(ValidationError, match="injection_first_exposure_turn"):
        grade(injection_exposed=exposed, injection_first_exposure_turn=turn)


def test_a_consistent_pair_is_accepted_and_the_runner_helper_never_builds_a_bad_one() -> None:
    assert grade(injection_exposed=True, injection_first_exposure_turn=3).injection_exposed
    assert Exposure(False).first_turn is None


def test_v1_records_parse_with_unknown_exposure() -> None:
    lines = (FIXTURES / "attempt_record_v1.jsonl").read_text().splitlines()
    records = [AttemptRecord.model_validate_json(line) for line in lines if line.strip()]
    assert records and all(r.injection_exposed is None for r in records)
    assert all(r.injection_first_exposure_turn is None for r in records)


def test_an_inconsistent_record_does_not_parse(tmp_path: Path) -> None:
    record = produce_records(tmp_path)[0]
    data = record.model_dump() | {"injection_exposed": False, "injection_first_exposure_turn": 2}
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate(data)


def produce_records(tmp_path: Path) -> list[AttemptRecord]:
    return run_attempts(tmp_path, default_responder)


# --- database, publisher ------------------------------------------------------------------------


def test_the_database_has_columns_for_exposure(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    db = tmp_path / "results.db"
    rebuild(world, db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
        assert {"injection_exposed", "injection_first_exposure_turn"} <= columns
        values = conn.execute(
            "SELECT DISTINCT injection_exposed FROM attempts WHERE task_category = 'injection'"
        ).fetchall()
    assert values == [(0,)]


def edit_jsonl(incoming: Path, change: Any) -> None:
    (path,) = (incoming / "runs").glob("*.jsonl")
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(change(row)) + "\n" for row in lines))


def test_the_publisher_accepts_records_with_exposure(tmp_path: Path) -> None:
    incoming = incoming_from(produce(tmp_path / "w", responder=reading_notes), tmp_path / "in")
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    publisher = ResultsPublisher(repo)
    publisher.apply(publisher.plan(incoming))
    published = [
        json.loads(line)
        for path in (repo / "runs").glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert any(r["injection_exposed"] is True for r in published)


def test_the_publisher_rejects_an_inconsistent_exposure(tmp_path: Path) -> None:
    incoming = incoming_from(produce(tmp_path / "w"), tmp_path / "in")
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    edit_jsonl(
        incoming, lambda r: r | {"injection_first_exposure_turn": 2, "injection_exposed": None}
    )
    rejects(incoming, repo, "jsonl")


# --- validate-tasks ---------------------------------------------------------------------------


def test_every_real_injection_task_names_markers_that_pass_the_validator() -> None:
    suite = TaskSuite.load(TASKS_DIR.parents[3] / "tasks")
    injection = [t for t in suite.all() if t.category == "injection"]
    assert {t.id for t in injection} >= {"fix-csv-total", "fix-settings-merge"}
    for task in injection:
        assert task.injection_markers
        assert TaskValidator.marker_problems(task) == []


def test_calibration_prints_the_exposure_line_for_injection_tasks(tmp_path: Path) -> None:
    records = baseline_records(tmp_path, trials=2)
    text = CalibrationReport.from_records(records).render()
    line = next(line for line in text.splitlines() if "injection:" in line)
    assert "exposed 0/2" in line and "resisted 0/0 among exposed (n/a)" in line
    assert "exposure unknown 0/2" in line


def test_calibration_counts_unknown_records_apart(tmp_path: Path) -> None:
    records = [
        r.model_copy(update={"injection_exposed": None}) if r.task_category == "injection" else r
        for r in baseline_records(tmp_path, trials=2)
    ]
    line = next(
        line
        for line in CalibrationReport.from_records(records).render().splitlines()
        if "injection:" in line
    )
    assert "exposure unknown 2/2 (resisted 2 of those)" in line
