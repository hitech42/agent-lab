import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from runner_support import (
    MONDAY,
    SCHEDULE_PATH,
    SUNDAY,
    FakeGrader,
    auth_error,
    build_cli,
    default_responder,
    status_error,
    write_schedule,
)

from agent_lab.runner.storage import ResultsStore

DAYS = {
    "2026-11-02": "model-comparison",
    "2026-11-03": "tool-design",
    "2026-11-04": "turn-budget",
    "2026-11-05": "plan-first",
    "2026-11-06": "self-verify",
    "2026-11-07": "tool-design",
    "2026-11-08": "aa-test",
}


def files_under(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*") if p.is_file()) if path.exists() else []


# --- dry run ---------------------------------------------------------------------------


def test_dry_run_prints_the_plan_and_changes_nothing(tmp_path: Path) -> None:
    world = build_cli(tmp_path, api_key=None)  # no key needed: no API calls
    code, output = world.run("--date", MONDAY, "--dry-run")

    assert code == 0
    assert "track: model-comparison" in output
    assert "question: Does claude-sonnet-5-5 solve more tasks" in output
    assert "baseline: baseline hash=" in output and "variant: model-comparison hash=" in output
    assert "plan (24 attempts, in this order):" in output
    assert "2026-11-02-fix-date-range-" in output and "estimate: attempts $" in output
    assert "no API calls were made and no files were written" in output
    assert world.client.requests == []
    assert files_under(tmp_path / "results") == [] and files_under(tmp_path / "transcripts") == []
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize(("day", "track"), sorted(DAYS.items()))
def test_dry_run_picks_the_track_for_each_weekday(tmp_path: Path, day: str, track: str) -> None:
    code, output = build_cli(tmp_path, api_key=None).run("--date", day, "--dry-run")
    iso = date.fromisoformat(day).isoweekday()
    assert code == 0 and f"track: {track}" in output and f"ISO weekday {iso}" in output


def test_dry_run_shows_identical_hashes_for_the_aa_test(tmp_path: Path) -> None:
    _, output = build_cli(tmp_path, api_key=None).run("--date", SUNDAY, "--dry-run")
    hashes = [line.split("hash=")[1].split()[0] for line in output.splitlines() if "hash=" in line]
    assert len(hashes) == 2 and hashes[0] == hashes[1]


def test_dry_run_warns_when_the_estimate_exceeds_the_daily_cap(tmp_path: Path) -> None:
    _, output = build_cli(tmp_path, api_key=None).run(
        "--date", MONDAY, "--trials", "40", "--dry-run"
    )
    assert "warning: the estimate exceeds the daily cap" in output


def test_dry_run_when_the_day_is_done_reports_it(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    _, output = world.run("--date", MONDAY, "--dry-run")
    assert "already done" in output


# --- a real (fake-model) run -------------------------------------------------------------


def test_a_run_writes_results_and_exits_zero(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    code, output = world.run("--date", MONDAY, "--trials", "1")
    store = ResultsStore(world.results_dir)
    assert code == 0 and "completed" in output and "completed 6/6" in output
    assert store.has_summary(MONDAY) and len(store.read_attempts(MONDAY)[0]) == 6
    transcripts = files_under(tmp_path / "transcripts")
    assert len(transcripts) == 6 and all(p.suffix == ".gz" for p in transcripts)


def test_tasks_trials_and_track_options(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    code, _ = world.run(
        "--date", MONDAY, "--trials", "2", "--tasks", "fix-date-range,add-top-flag",
        "--track", "aa-test",
    )  # fmt: skip
    records = ResultsStore(world.results_dir).read_attempts(MONDAY)[0]
    assert code == 0 and len(records) == 2 * 2 * 2
    assert {r.task_id for r in records} == {"fix-date-range", "add-top-flag"}
    assert {r.track for r in records} == {"aa-test"}


def test_only_the_selected_date_is_written(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", "2026-11-05", "--trials", "1")
    names = sorted(p.name for p in (world.results_dir / "runs").iterdir())
    assert names == ["2026-11-05.jsonl", "2026-11-05.run.json"]


# --- idempotency and resume --------------------------------------------------------------


def test_a_finished_day_is_never_run_again(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    calls = len(world.client.requests)
    before = {p: p.read_bytes() for p in files_under(world.results_dir)}

    code, output = world.run("--date", MONDAY, "--trials", "1")
    assert code == 0 and "already done" in output
    assert len(world.client.requests) == calls  # no API calls
    assert {p: p.read_bytes() for p in files_under(world.results_dir)} == before


def test_already_done_needs_no_api_key(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    world.environ.clear()
    code, output = world.run("--date", MONDAY, "--trials", "1", "--resume")
    assert code == 0 and "already done" in output


def interrupted_day(tmp_path: Path) -> Any:
    """A day whose JSONL exists without a run.json (a run that was cut short)."""
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    ResultsStore(world.results_dir).summary_path(MONDAY).unlink()
    return world


def test_a_jsonl_without_a_summary_is_refused_without_resume(tmp_path: Path) -> None:
    world = interrupted_day(tmp_path)
    calls = len(world.client.requests)
    before = world.results_dir.joinpath("runs", f"{MONDAY}.jsonl").read_bytes()
    code, output = world.run("--date", MONDAY, "--trials", "1")
    assert code == 1 and "interrupted run" in output and "--resume" in output
    assert len(world.client.requests) == calls
    assert world.results_dir.joinpath("runs", f"{MONDAY}.jsonl").read_bytes() == before


def test_resume_skips_recorded_attempts_and_appends_the_rest(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    store = ResultsStore(world.results_dir)
    path = store.attempts_path(MONDAY)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:3]) + "\n")  # as if only 3 attempts had been written
    store.summary_path(MONDAY).unlink()
    before = path.read_bytes()
    world.client.requests.clear()

    code, output = world.run("--date", MONDAY, "--trials", "1", "--resume")
    assert code == 0 and "completed 6/6" in output
    assert path.read_bytes().startswith(before)
    assert len(store.read_attempts(MONDAY)[0]) == 6
    assert len(world.client.attempts) == 3  # only the missing three were run
    assert store.has_summary(MONDAY)


def test_resume_on_a_fresh_date_behaves_like_a_normal_run(tmp_path: Path) -> None:
    normal = build_cli(tmp_path / "a")
    resumed = build_cli(tmp_path / "b")
    normal.run("--date", MONDAY, "--trials", "1")
    code, _ = resumed.run("--date", MONDAY, "--trials", "1", "--resume")

    def shape(world: Any) -> list[tuple[Any, ...]]:
        records = ResultsStore(world.results_dir).read_attempts(MONDAY)[0]
        return [(r.attempt_id, r.status, r.config_hash, round(r.cost_usd, 9)) for r in records]

    assert code == 0 and shape(normal) == shape(resumed)
    assert ResultsStore(resumed.results_dir).read_summaries()[0].abort_count == 0


def test_phase_four_style_resume_after_an_abort(tmp_path: Path) -> None:
    """The scheduler always passes --resume: an aborted day continues on the next run."""
    world = build_cli(tmp_path, responder=lambda request: auth_error())
    code, output = world.run("--date", MONDAY, "--trials", "1", "--resume")
    assert code == 0 and "ABORTED (warmup_failed)" in output and "--resume" in output
    store = ResultsStore(world.results_dir)
    assert not store.has_summary(MONDAY) and len(store.read_aborts(MONDAY)) == 1

    world.client.responder = default_responder  # the key works again
    code, output = world.run("--date", MONDAY, "--trials", "1", "--resume")
    summary = store.read_summaries()[0]
    assert code == 0 and summary.status == "completed" and summary.abort_count == 1


def test_abort_event_exits_zero(tmp_path: Path) -> None:
    world = build_cli(tmp_path, responder=lambda request: status_error(500))
    code, output = world.run("--date", MONDAY, "--trials", "1")
    assert code == 0 and "ABORTED" in output and "APIStatusError" in output


def test_terminal_statuses_exit_zero(tmp_path: Path) -> None:
    tampered = build_cli(tmp_path / "t", grader=FakeGrader(tamper_from_call=1))
    code, output = tampered.run("--date", MONDAY, "--trials", "1")
    assert code == 0 and "aborted_tampered" in output
    again, output = tampered.run("--date", MONDAY, "--trials", "1", "--resume")
    assert again == 0 and "already done" in output  # tampering is final


# --- safety ---------------------------------------------------------------------------


def test_past_results_are_never_modified(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", "2026-11-02", "--trials", "1")
    snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files_under(world.results_dir)}
    world.run("--date", "2026-11-03", "--trials", "1")
    for path, (content, mtime) in snapshot.items():
        assert path.read_bytes() == content and path.stat().st_mtime_ns == mtime


def test_a_missing_key_is_a_clear_error_and_writes_nothing(tmp_path: Path) -> None:
    world = build_cli(tmp_path, api_key=None)
    code, output = world.run("--date", MONDAY, "--trials", "1")
    assert code == 1 and "ANTHROPIC_API_KEY is not set" in output
    assert world.client.requests == [] and files_under(tmp_path / "results") == []


def test_the_api_key_is_never_printed_or_written(tmp_path: Path) -> None:
    key = "sk-ant-api03-TOPSECRETKEYVALUE"
    world = build_cli(tmp_path, api_key=key)
    _, dry = world.run("--date", MONDAY, "--trials", "1", "--dry-run")
    _, real = world.run("--date", MONDAY, "--trials", "1")
    assert "TOPSECRET" not in dry and "TOPSECRET" not in real
    for path in files_under(tmp_path):
        if path.suffix == ".gz":
            continue
        assert "TOPSECRET" not in path.read_text(errors="ignore")


def test_there_is_no_command_line_flag_for_the_api_key(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        world.run("--date", MONDAY, "--api-key", "x")
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--date", "yesterday"), "--date must be a real date as YYYY-MM-DD"),
        (("--date", "2026-13-45"), "--date must be a real date as YYYY-MM-DD"),
        (("--date", MONDAY, "--track", "nope"), "unknown track 'nope'"),
        (("--date", MONDAY, "--tasks", "nope"), "unknown task"),
    ],
)
def test_configuration_errors_exit_nonzero_and_write_nothing(
    tmp_path: Path, args: tuple[str, ...], message: str
) -> None:
    world = build_cli(tmp_path)
    code, output = world.run(*args)
    assert code == 1 and message in output
    assert world.client.requests == [] and files_under(tmp_path / "results") == []


def test_an_invalid_schedule_is_a_configuration_error(tmp_path: Path) -> None:
    path = write_schedule(
        tmp_path, lambda raw: raw["tracks"][1]["variant"].update({"model": "gpt-9"})
    )
    world = build_cli(tmp_path)
    argv = ["run", "--date", MONDAY, "--schedule", str(path), "--results-dir", str(tmp_path / "r")]
    code = world.cli.run([*argv, "--tasks-dir", str(SCHEDULE_PATH.parents[1] / "tasks")])
    assert code == 1 and "invalid schedule" in world.out.getvalue()
    assert world.client.requests == []


def test_failed_grades_and_errors_do_not_change_the_exit_code(tmp_path: Path) -> None:
    world = build_cli(tmp_path, grader=FakeGrader(passed=False))
    code, _ = world.run("--date", MONDAY, "--trials", "1")
    assert code == 0


def test_the_summary_json_is_valid_and_complete(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    data = json.loads(ResultsStore(world.results_dir).summary_path(MONDAY).read_text())
    assert set(data) >= {
        "run_id", "date", "track", "status", "started", "finished", "planned", "completed",
        "errored", "skipped_budget", "skipped_aborted", "skipped_attempts", "abort_count",
        "warmup_cost_usd", "total_cost_usd", "estimate_usd", "schema_version",
    }  # fmt: skip
