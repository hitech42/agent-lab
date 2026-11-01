"""Old result files must keep parsing (the schema-compatibility rule in CLAUDE.md).

The files in fixtures/ were written by the first released schema (v1) and must never be
edited. New fields need defaults so that these files stay valid.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from runner_support import MONDAY

from agent_lab.runner.database import ResultsDatabase
from agent_lab.runner.records import AbortRecord, AttemptRecord, RunSummary
from agent_lab.runner.storage import ResultsStore

FIXTURES = Path(__file__).parent / "fixtures"
ATTEMPTS = FIXTURES / "attempt_record_v1.jsonl"
SUMMARY = FIXTURES / "run_summary_v1.json"
ABORT = FIXTURES / "abort_record_v1.json"


def attempt_lines() -> list[str]:
    return [line for line in ATTEMPTS.read_text().splitlines() if line.strip()]


def test_v1_attempt_records_still_parse() -> None:
    records = [AttemptRecord.model_validate_json(line) for line in attempt_lines()]
    assert len(records) == 2 and {r.schema_version for r in records} == {1}
    assert all(r.effort is None and r.git_dirty is None for r in records)  # fields added later


def test_v1_summary_and_abort_still_parse() -> None:
    summary = RunSummary.model_validate_json(SUMMARY.read_text())
    abort = AbortRecord.model_validate_json(ABORT.read_text())
    assert summary.schema_version == abort.schema_version == 1
    assert summary.corrupt_lines == 0 and abort.corrupt_lines == 0  # added later


@pytest.mark.parametrize(
    ("model", "sample"),
    [
        (AttemptRecord, json.loads(attempt_lines()[0])),
        (RunSummary, json.loads(SUMMARY.read_text())),
        (AbortRecord, json.loads(ABORT.read_text())),
    ],
)
def test_every_field_missing_from_a_v1_file_has_a_default(model, sample) -> None:  # type: ignore[no-untyped-def]
    unknown = set(sample) - set(model.model_fields)
    assert not unknown, f"fields were removed or renamed: {unknown}"
    required_but_new = [
        name
        for name, field in model.model_fields.items()
        if name not in sample and field.is_required()
    ]
    assert not required_but_new, f"new fields without a default break old files: {required_but_new}"


def test_old_and_new_lines_share_one_file(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.attempts_path(MONDAY).parent.mkdir(parents=True)
    store.attempts_path(MONDAY).write_text(ATTEMPTS.read_text())
    newer = AttemptRecord.model_validate(
        {
            **json.loads(attempt_lines()[0]),
            "attempt_id": "2026-11-02-new-line",
            "effort": "medium",
            "git_dirty": False,
        }
    )
    store.append_attempt(newer)  # appended after the old lines, as a resumed day would
    records, corrupt = store.read_attempts(MONDAY)
    assert corrupt == 0 and len(records) == 3
    assert [r.effort for r in records] == [None, None, "medium"]


def test_the_database_rebuilds_from_old_and_new_files_together(tmp_path: Path) -> None:
    runs = tmp_path / "results" / "runs"
    runs.mkdir(parents=True)
    (runs / f"{MONDAY}.jsonl").write_text(ATTEMPTS.read_text())
    (runs / f"{MONDAY}.run.json").write_text(SUMMARY.read_text())
    (runs / f"{MONDAY}.abort-090000.json").write_text(ABORT.read_text())
    db = tmp_path / "results.db"
    report = ResultsDatabase().rebuild(ResultsStore(tmp_path / "results"), db)
    assert (report.attempts, report.runs, report.aborts, report.corrupt_lines) == (2, 1, 1, 0)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT effort, git_dirty FROM attempts").fetchall() == [
            (None, None),
            (None, None),
        ]
