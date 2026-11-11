import re
from pathlib import Path

import pytest
from runner_support import (
    CREDIT_MESSAGE,
    MONDAY,
    FakeGrader,
    bad_request,
    build_cli,
    build_world,
    default_responder,
    is_warmup,
    status_error,
    write_schedule,
)

from agent_lab.harness.redact import Redactor
from agent_lab.runner.outcome import SAFE_VALUE, RunReport
from agent_lab.runner.storage import ResultsStore

LINE = re.compile(rf"^(kind|status|detail|completed|planned)=({SAFE_VALUE})$")


def read(path: Path) -> dict[str, str]:
    """The outcome file as the workflow reads it: every line must pass the safe-value filter."""
    lines = path.read_text().splitlines()
    assert all(LINE.match(line) for line in lines), lines
    return dict(line.split("=", 1) for line in lines)


def run_with(tmp_path: Path, *argv: str, **world: object) -> dict[str, str]:
    cli = build_cli(tmp_path, **world)  # type: ignore[arg-type]
    outcome = tmp_path / "outcome"
    code, _ = cli.run("--date", MONDAY, "--trials", "1", "--outcome-file", str(outcome), *argv)
    assert code == 0
    return read(outcome)


# --- the report written by `agent-lab run` ------------------------------------------------


def test_a_completed_day_reports_its_summary_status_and_counts(tmp_path: Path) -> None:
    assert run_with(tmp_path) == {
        "kind": "summary",
        "status": "completed",
        "detail": "",
        "completed": "6",
        "planned": "6",
    }


def test_a_tampered_day_reports_aborted_tampered(tmp_path: Path) -> None:
    report = run_with(tmp_path, grader=FakeGrader(tamper_from_call=1))
    assert (report["kind"], report["status"]) == ("summary", "aborted_tampered")


def test_a_budget_stop_reports_partial_budget_with_the_counts(tmp_path: Path) -> None:
    def tight(raw: dict) -> None:  # type: ignore[type-arg]
        raw["tracks"][1]["daily_max_cost_usd"] = 0.07  # Monday: room for one pair, not two

    schedule = write_schedule(tmp_path, tight)
    report = run_with(tmp_path, "--schedule", str(schedule))
    assert (report["kind"], report["status"]) == ("summary", "partial_budget")
    assert (report["completed"], report["planned"]) == ("2", "6")


def test_a_warmup_failure_reports_the_reason_status_and_message(tmp_path: Path) -> None:
    report = run_with(tmp_path, responder=lambda request: bad_request())
    assert report["kind"] == "abort" and report["status"] == "warmup_failed"
    assert report["detail"].startswith("400 BadRequestError: Your credit balance is too low")
    assert (report["completed"], report["planned"]) == ("0", "6")


def test_a_credit_balance_error_in_the_attempts_has_its_own_status(tmp_path: Path) -> None:
    state = {"n": 0}

    def responder(request: dict) -> object:  # type: ignore[type-arg]
        if is_warmup(request):
            return default_responder(request)
        state["n"] += 1
        return bad_request() if state["n"] == 2 else default_responder(request)

    report = run_with(tmp_path, responder=responder)
    assert (report["kind"], report["status"]) == ("abort", "credit_balance")
    assert report["completed"] == "2"


def test_an_abort_without_a_message_still_reports(tmp_path: Path) -> None:
    report = run_with(tmp_path, responder=lambda request: status_error(500))
    assert report["status"] == "warmup_failed" and report["detail"].startswith("500 APIStatusError")


def test_an_already_done_day_reports_already_done(tmp_path: Path) -> None:
    cli = build_cli(tmp_path)
    cli.run("--date", MONDAY, "--trials", "1")
    outcome = tmp_path / "outcome"
    cli.run("--date", MONDAY, "--trials", "1", "--outcome-file", str(outcome))
    assert read(outcome) == {"kind": "already_done", "status": "already_done", "detail": ""}


def test_a_dry_run_reports_dry_run_and_still_writes_no_results(tmp_path: Path) -> None:
    cli = build_cli(tmp_path, api_key=None)
    outcome = tmp_path / "outcome"
    code, _ = cli.run("--date", MONDAY, "--dry-run", "--outcome-file", str(outcome))
    assert code == 0 and read(outcome) == {"kind": "dry_run", "status": "dry_run", "detail": ""}
    assert not ResultsStore(cli.results_dir).has_attempts(MONDAY)


def test_no_outcome_file_without_the_option_and_none_on_configuration_errors(
    tmp_path: Path,
) -> None:
    cli = build_cli(tmp_path)
    cli.run("--date", MONDAY, "--trials", "1")
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("outcome")] == []
    outcome = tmp_path / "outcome"
    code, _ = cli.run("--date", "not-a-date", "--outcome-file", str(outcome))
    assert code == 1 and not outcome.exists()
    code, _ = cli.run("--date", MONDAY, "--trials", "0", "--outcome-file", str(outcome))
    assert code == 1 and not outcome.exists()


def test_an_abort_still_exits_zero_so_the_artifact_is_collected(tmp_path: Path) -> None:
    cli = build_cli(tmp_path, responder=lambda request: bad_request())
    code, output = cli.run("--date", MONDAY, "--trials", "1")
    assert code == 0 and "ABORTED" in output


# --- what can reach the workflow ----------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "boom\n::error::fake alert\n::set-output name=x::y",
        "100%0A::warning::nope %0D%0A",
        "key sk-ant-api03-SECRETSECRET and ghp_abcdef123456 here",
        "line one\r\nline two\ttabbed `backtick` $(rm -rf /) ${{ secrets.X }} <b>",
        "é ünïcode " + "x" * 500,
    ],
)
def test_a_hostile_error_message_cannot_inject_into_the_workflow(
    tmp_path: Path, message: str
) -> None:
    world = build_world(tmp_path, responder=lambda request: bad_request(message))
    report = RunReport.of(world.runner.run(world.plan), Redactor(["SECRETSECRET"]))
    assert report.alert
    text = "\n".join(report.lines())
    assert all(LINE.match(line) for line in report.lines()), report.lines()
    assert len(report.detail) <= 160
    for bad in ("::set-output", "%0A", "sk-ant", "ghp_", "SECRETSECRET", "\n\n", "${{", "`", "$("):
        assert bad not in text, bad
    assert report.detail.count("\n") == 0 and report.status == "warmup_failed"


def test_the_secret_in_a_message_is_redacted_before_it_is_cleaned(tmp_path: Path) -> None:
    world = build_world(tmp_path, responder=lambda request: bad_request("bad key hunter2-abc here"))
    report = RunReport.of(world.runner.run(world.plan), Redactor(["hunter2-abc"]))
    assert "hunter2" not in report.detail and "REDACTED" in report.detail


def test_the_detail_is_capped_and_the_cap_is_a_parameter(tmp_path: Path) -> None:
    world = build_world(tmp_path, responder=lambda request: bad_request(CREDIT_MESSAGE))
    outcome = world.runner.run(world.plan)
    assert len(RunReport.of(outcome, Redactor([])).detail) <= 160
    assert len(RunReport.of(outcome, Redactor([]), max_detail=20).detail) == 20


# --- the alert decision --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("report", "alert"),
    [
        (RunReport("abort", "warmup_failed"), True),
        (RunReport("abort", "credit_balance"), True),
        (RunReport("summary", "aborted_tampered"), True),
        (RunReport("summary", "completed"), False),
        (RunReport("summary", "partial_budget"), False),
        (RunReport.already_done(), False),
        (RunReport.dry_run(), False),
    ],
)
def test_only_aborts_and_tampering_alert(report: RunReport, alert: bool) -> None:
    assert report.alert is alert


def test_counts_are_written_only_when_known() -> None:
    assert RunReport.dry_run().lines() == ["kind=dry_run", "status=dry_run", "detail="]
    assert RunReport("summary", "partial_budget", "", 20, 32).lines()[-2:] == [
        "completed=20",
        "planned=32",
    ]
