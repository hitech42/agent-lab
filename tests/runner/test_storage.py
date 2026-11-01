import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from runner_support import MONDAY, PROVENANCE, make_plan, schedule

from agent_lab.benchmark.grader import GradeResult
from agent_lab.benchmark.manifest import ChangedFiles
from agent_lab.harness.agent import AttemptResult
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.redact import Redactor
from agent_lab.harness.tracing import AttemptTrace
from agent_lab.runner.records import (
    AbortRecord,
    AttemptRecord,
    ConfigRecord,
    RecordBuilder,
    RunSummary,
)
from agent_lab.runner.schedule import ExperimentSchedule
from agent_lab.runner.storage import ResultsStore, TranscriptStore


def record(index: int = 0, day: str = MONDAY) -> AttemptRecord:
    plan = make_plan(day, trials=1)
    planned = plan.attempts[index]
    result = AttemptResult(
        status="completed",
        stop_reason="end_turn",
        final_text="",
        trace=AttemptTrace(),
        cost_usd=0.01,
    )
    grade = GradeResult(
        passed=True, tests_passed=1, tests_failed=0, tests_errors=0, tests_total=1,
        protected_violations=[], canaries_triggered=[], injection_resisted=None,
        changed_files=ChangedFiles(), detail="",
    )  # fmt: skip
    builder = RecordBuilder("r", day, "t", "q", PROVENANCE, Redactor([]))
    return builder.build(
        planned, planned.task_id, "bugfix", schedule().baseline, "h", result=result, grade=grade,
    )  # fmt: skip


def summary(day: str = MONDAY) -> RunSummary:
    return RunSummary(
        run_id="r", date=day, track="t", status="completed", started="s", finished="f",
        planned=1, completed=1, errored=0, skipped_budget=0, skipped_aborted=0,
        warmup_cost_usd=0.0, total_cost_usd=0.0, estimate_usd=0.0,
    )  # fmt: skip


def abort(day: str = MONDAY) -> AbortRecord:
    return AbortRecord(**summary(day).model_dump(), reason="auth_error")


def test_append_writes_one_line_per_record(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.append_attempt(record(0))
    store.append_attempt(record(1))
    lines = store.attempts_path(MONDAY).read_text().splitlines()
    assert len(lines) == 2
    assert [AttemptRecord.model_validate_json(line).order for line in lines] == [1, 2]
    assert store.attempts_path(MONDAY) == tmp_path / "runs" / f"{MONDAY}.jsonl"


def test_append_never_rewrites_earlier_bytes(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.append_attempt(record(0))
    before = store.attempts_path(MONDAY).read_bytes()
    store.append_attempt(record(1))
    assert store.attempts_path(MONDAY).read_bytes().startswith(before)


def test_reading_returns_records_and_counts_unreadable_lines(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.append_attempt(record(0))
    with store.attempts_path(MONDAY).open("a") as handle:
        handle.write("{not json}\n\n")
        handle.write(json.dumps({"schema_version": 1}) + "\n")
    store.append_attempt(record(1))
    records, corrupt = store.read_attempts(MONDAY)
    assert [r.order for r in records] == [1, 2] and corrupt == 2
    assert store.recorded_attempt_ids(MONDAY) == {r.attempt_id for r in records}


def test_a_line_cut_short_by_a_killed_writer_does_not_swallow_the_next_record(
    tmp_path: Path,
) -> None:
    store = ResultsStore(tmp_path)
    store.append_attempt(record(0))
    with store.attempts_path(MONDAY).open("a") as handle:
        handle.write('{"schema_version": 1, "run_i')  # no trailing newline
    store.append_attempt(record(1))
    records, corrupt = store.read_attempts(MONDAY)
    assert [r.order for r in records] == [1, 2] and corrupt == 1


def test_missing_files_read_as_empty(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    assert store.read_attempts(MONDAY) == ([], 0)
    assert store.read_all_attempts() == ([], 0)
    assert store.read_aborts() == ([], 0) and store.read_summaries() == ([], 0)
    assert not store.has_attempts(MONDAY) and not store.has_summary(MONDAY)


def test_read_all_spans_every_date(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.append_attempt(record(0, "2026-11-02"))
    store.append_attempt(record(0, "2026-11-03"))
    records, corrupt = store.read_all_attempts()
    assert [r.date for r in records] == ["2026-11-02", "2026-11-03"] and corrupt == 0


def test_the_summary_is_created_once_and_never_overwritten(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    path = store.write_summary(summary())
    assert path.name == f"{MONDAY}.run.json" and store.has_summary(MONDAY)
    assert store.read_summaries() == ([summary()], 0)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        store.write_summary(summary().model_copy(update={"planned": 99}))
    assert path.read_bytes() == before


def test_abort_files_are_named_by_time_and_never_clash(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    at = datetime(2026, 11, 2, 9, 30, 5, tzinfo=UTC)
    first = store.write_abort(abort(), at)
    second = store.write_abort(abort(), at)  # same second: moves on instead of overwriting
    third = store.write_abort(abort(), datetime(2026, 11, 2, 23, 59, 59, tzinfo=UTC))
    assert [p.name for p in (first, second, third)] == [
        f"{MONDAY}.abort-093005.json",
        f"{MONDAY}.abort-093006.json",
        f"{MONDAY}.abort-235959.json",
    ]
    assert len(store.read_aborts(MONDAY)[0]) == 3 and store.read_aborts("2026-11-03") == ([], 0)
    assert not store.has_summary(MONDAY)  # an abort is not a summary


def test_nothing_in_the_store_can_delete_or_truncate() -> None:
    public = {n for n in dir(ResultsStore) if not n.startswith("_")}
    assert not {n for n in public if "delete" in n or "remove" in n or "truncate" in n}


def test_transcripts_are_gzipped_json_under_the_date(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "t")
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"x": 1}]}]
    assert store.write(MONDAY, "attempt-1", messages)
    path = tmp_path / "t" / MONDAY / "attempt-1.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert json.load(handle) == messages


def test_a_transcript_failure_is_a_warning_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    assert TranscriptStore(blocker).write(MONDAY, "a", []) is False
    assert "could not write transcript" in capsys.readouterr().err


# --- reading bytes, line by line ------------------------------------------------------


def test_a_multibyte_character_cut_in_the_last_line_costs_only_that_line(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.append_attempt(record(0))
    with store.attempts_path(MONDAY).open("ab") as handle:
        handle.write('{"detail": "caf\u00e9'.encode()[:-1])  # killed inside the two-byte "é"
    store.append_attempt(record(1))
    records, corrupt = store.read_attempts(MONDAY)
    assert [r.order for r in records] == [1, 2] and corrupt == 1


def test_a_line_that_is_not_utf8_is_one_corrupt_line(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.append_attempt(record(0))
    with store.attempts_path(MONDAY).open("ab") as handle:
        handle.write(b"\xff\xfe not text\n")
    store.append_attempt(record(1))
    records, corrupt = store.read_attempts(MONDAY)
    assert [r.order for r in records] == [1, 2] and corrupt == 1
    assert store.read_all_attempts() == (records, 1)


def test_unicode_line_separators_inside_a_record_do_not_split_it(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    odd = record(0).model_copy(update={"detail": "a\u2028b\u0085c\x0cd\x1ce"})
    store.append_attempt(odd)
    store.append_attempt(record(1))
    records, corrupt = store.read_attempts(MONDAY)
    assert corrupt == 0 and len(records) == 2 and records[0].detail == odd.detail


# --- complete writes ------------------------------------------------------------------


def test_write_all_keeps_writing_until_every_byte_is_out() -> None:
    chunks: list[bytes] = []

    def stingy(fd: int, view: memoryview) -> int:
        chunks.append(bytes(view[:7]))
        return len(chunks[-1])

    ResultsStore._write_all(1, bytes(range(30)), write=stingy)
    assert b"".join(chunks) == bytes(range(30)) and len(chunks) == 5


def test_write_all_fails_instead_of_spinning_when_nothing_is_written() -> None:
    with pytest.raises(OSError, match="no progress"):
        ResultsStore._write_all(1, b"abc", write=lambda fd, view: 0)


def test_append_survives_a_partial_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_write = os.write

    def stingy(fd: int, data: bytes | memoryview) -> int:
        return real_write(fd, bytes(data[:5]))

    store = ResultsStore(tmp_path)
    monkeypatch.setattr(os, "write", stingy)
    store.append_attempt(record(0))
    monkeypatch.undo()
    records, corrupt = store.read_attempts(MONDAY)
    assert len(records) == 1 and corrupt == 0


# --- unreadable files -----------------------------------------------------------------


def test_unreadable_abort_and_summary_files_are_counted_not_raised(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    store.write_abort(abort(), datetime(2026, 11, 2, 9, 0, 0, tzinfo=UTC))
    store.write_summary(summary("2026-11-02"))
    runs = store.runs_dir
    (runs / f"{MONDAY}.abort-100000.json").write_text("{not json")
    (runs / f"{MONDAY}.abort-110000.json").write_bytes(b"\xff\xfe")
    (runs / f"{MONDAY}.abort-120000.json").mkdir()  # cannot be read as a file at all
    (runs / "2026-11-03.run.json").write_text('{"schema_version": 1}')
    aborts, bad_aborts = store.read_aborts(MONDAY)
    assert len(aborts) == 1 and bad_aborts == 3
    summaries, bad_summaries = store.read_summaries()
    assert len(summaries) == 1 and bad_summaries == 1


# --- config registry ------------------------------------------------------------------


def test_a_config_is_registered_once_and_never_overwritten(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    config = schedule().baseline
    digest = ExperimentSchedule.config_hash(config)
    assert store.register_config(digest, config) is True
    path = store.configs_dir / f"{digest}.json"
    assert path == tmp_path / "configs" / f"{digest}.json"
    before = path.read_bytes()
    assert store.register_config(digest, config.model_copy(update={"name": "renamed"})) is False
    assert path.read_bytes() == before


def test_the_registered_file_holds_exactly_what_the_hash_covers(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    config = schedule().baseline
    digest = ExperimentSchedule.config_hash(config)
    store.register_config(digest, config)
    data = json.loads((store.configs_dir / f"{digest}.json").read_text())
    assert "name" not in data and data["first_name"] == "baseline"
    assert data["system_prompt"] == config.system_prompt and data["effort"] == "medium"
    inputs = {
        k: v for k, v in data.items() if k not in {"schema_version", "config_hash", "first_name"}
    }
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canonical.encode()).hexdigest()[:12] == digest == data["config_hash"]


def test_every_agent_config_field_is_stored_in_the_registry() -> None:
    assert set(AgentConfig.model_fields) - {"name"} <= set(ConfigRecord.model_fields)


def test_registered_configs_are_read_back_and_bad_files_counted(tmp_path: Path) -> None:
    store = ResultsStore(tmp_path)
    assert store.read_configs() == ([], 0)
    config = schedule().baseline
    store.register_config("abc123abc123", config)
    (store.configs_dir / "bad.json").write_text("nope")
    configs, bad = store.read_configs()
    assert [c.config_hash for c in configs] == ["abc123abc123"] and bad == 1
