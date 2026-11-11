"""The CI workflow keeps the guarantees we rely on (cheap, no network, no secrets)."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(WORKFLOW.read_text())


def test_pushes_run_on_main_only_and_pull_requests_always() -> None:
    triggers = load()[True]  # YAML parses the bare key `on` as the boolean True
    assert triggers["push"] == {"branches": ["main"]}
    assert "pull_request" in triggers


def test_the_job_has_a_timeout_and_does_not_keep_credentials() -> None:
    job = load()["jobs"]["check"]
    assert job["timeout-minutes"] == 20
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["persist-credentials"] is False


def test_scripts_are_type_checked_right_after_the_sources() -> None:
    runs = [s["run"] for s in load()["jobs"]["check"]["steps"] if "run" in s]
    assert runs.index("uv run mypy scripts/") == runs.index("uv run mypy") + 1


def test_ci_never_receives_the_api_key() -> None:
    assert "ANTHROPIC_API_KEY" not in WORKFLOW.read_text()


def test_the_isolation_job_uses_the_opt_layout_and_the_setup_script() -> None:
    job = load()["jobs"]["isolation"]
    assert job["env"] == {
        "UV_PROJECT_ENVIRONMENT": "/opt/agentlab/venv",
        "UV_PYTHON_INSTALL_DIR": "/opt/agentlab/python",
    }
    runs = [s["run"] for s in job["steps"] if "run" in s]
    order = [
        r for r in runs if r in {"uv python install", "uv sync --locked"} or "setup_sandbox" in r
    ]
    assert order[0] == "uv python install" and order[1] == "uv sync --locked"
    assert "sudo scripts/setup_sandbox_user.sh" in order[2]
    assert runs[-1] == "uv run pytest -m isolation"


# --- the daily workflow -------------------------------------------------------------------------

DAILY = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml"


def daily() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(DAILY.read_text())


def steps_of(job: str) -> list[dict]:  # type: ignore[type-arg]
    return daily()["jobs"][job]["steps"]


def test_daily_triggers_are_only_schedule_and_manual_dispatch() -> None:
    triggers = daily()[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}  # never push or pull_request
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["type"] == "boolean" and inputs["dry_run"]["default"] is False


def test_daily_has_a_primary_and_a_backup_slot_at_non_round_minutes() -> None:
    """GitHub starts scheduled runs late or not at all, so there is a backup slot."""
    crons = [entry["cron"] for entry in daily()[True]["schedule"]]
    assert len(crons) == 2
    hours = []
    for cron in crons:
        minute, hour, *rest = cron.split()
        assert rest == ["*", "*", "*"] and minute not in {"0", "30", "15", "45"}
        assert minute.isdigit() and hour.isdigit()
        hours.append(int(hour))
    assert len(set(hours)) == len(hours)  # one slot per hour at most
    hours.sort()
    gaps = [(b - a) for a, b in zip(hours, [*hours[1:], hours[0] + 24], strict=True)]
    assert max(gaps) <= 18  # the backup is hours after the primary, not in the same hour


def test_daily_runs_are_serialized_and_never_cancelled() -> None:
    assert daily()["concurrency"] == {"group": "daily-experiment", "cancel-in-progress": False}


def test_the_experiment_job_can_only_read_and_keeps_no_credentials() -> None:
    job = daily()["jobs"]["experiment"]
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] == 60 and job["environment"] == "experiment"
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["persist-credentials"] is False
    assert job["env"] == {
        "UV_PROJECT_ENVIRONMENT": "/opt/agentlab/venv",
        "UV_PYTHON_INSTALL_DIR": "/opt/agentlab/python",
    }


def test_the_experiment_job_sets_up_the_sandbox_and_runs_isolated_and_resumable() -> None:
    runs = [s["run"] for s in steps_of("experiment") if "run" in s]
    order = [
        r for r in runs if r in {"uv python install", "uv sync --locked"} or "setup_sandbox" in r
    ]
    assert order[0] == "uv python install" and order[1] == "uv sync --locked"
    assert order[2] == "sudo scripts/setup_sandbox_user.sh"
    run_step = next(s for s in steps_of("experiment") if "agent-lab run" in s.get("run", ""))
    assert "--isolation linux-user" in run_step["run"] and "--resume" in run_step["run"]
    assert "--dry-run" in run_step["run"]  # added when the dry_run input is set
    assert "--isolation none" not in run_step["run"]


def test_the_api_key_exists_only_in_the_run_step_of_the_experiment_job() -> None:
    text = DAILY.read_text()
    assert text.count("secrets.") == 1 and text.count("ANTHROPIC_API_KEY") == 2  # name + value
    holders = [
        s
        for job in daily()["jobs"]
        for s in steps_of(job)
        if "ANTHROPIC_API_KEY" in s.get("env", {})
    ]
    assert len(holders) == 1 and "agent-lab run" in holders[0]["run"]
    assert holders[0]["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert "env" not in daily() and "env" not in daily()["jobs"]["publish"]


def test_transcripts_are_never_uploaded_and_only_the_collected_files_are() -> None:
    uploads = [
        s for s in steps_of("experiment") if str(s.get("uses", "")).startswith("actions/upload")
    ]
    assert len(uploads) == 1
    upload = uploads[0]["with"]
    assert "transcripts" not in str(upload) and upload["path"].endswith("/incoming")
    assert upload["retention-days"] == 3
    collect_step = next(s for s in steps_of("experiment") if s.get("id") == "collect")
    code = "\n".join(
        line for line in collect_step["run"].splitlines() if not line.lstrip().startswith("#")
    )
    assert "-- runs configs" in code and "transcripts" not in code
    assert "!cancelled()" in collect_step["if"]  # runs even after a failing `agent-lab run`


def test_the_upload_is_skipped_when_nothing_changed() -> None:
    upload = next(s for s in steps_of("experiment") if "upload-artifact" in s.get("uses", ""))
    assert "changed == 'true'" in upload["if"]


def test_the_publish_job_can_push_but_never_sees_the_key_or_runs_agent_code() -> None:
    job = daily()["jobs"]["publish"]
    assert job["permissions"] == {"contents": "write"} and job["timeout-minutes"] == 10
    assert job["needs"] == "experiment" and "environment" not in job
    text = yaml.safe_dump(job)
    for forbidden in ("ANTHROPIC", "secrets.", "setup_sandbox", "agent-lab run", "isolation"):
        assert forbidden not in text, forbidden
    assert "changed == 'true'" in job["if"] and "inputs.dry_run != true" in job["if"]


def test_the_publish_job_commits_only_results_with_the_configured_identity() -> None:
    text = "\n".join(s.get("run", "") for s in steps_of("publish"))
    assert re.findall(r"git add .+", text) == ["git add results/ report/ README.md"]
    assert "results publish" in text and "results commit-message" in text
    assert "git commit -F" in text and "-m " not in text.split("git commit")[1].split("\n")[0]
    assert text.count("git push") == 1 and "git pull --rebase" in text and "1 2 3" in text
    env = next(s for s in steps_of("publish") if s.get("name") == "Commit and push")["env"]
    for var in ("GIT_AUTHOR", "GIT_COMMITTER"):
        assert env[f"{var}_NAME"] == "${{ vars.COMMIT_AUTHOR_NAME }}"
        assert env[f"{var}_EMAIL"] == "${{ vars.COMMIT_AUTHOR_EMAIL }}"


def test_the_publish_job_fails_clearly_when_the_author_variables_are_unset() -> None:
    first = steps_of("publish")[0]
    assert "COMMIT_AUTHOR_NAME" in str(first["env"]) and "COMMIT_AUTHOR_EMAIL" in str(first["env"])
    assert "::error::" in first["run"] and "exit 1" in first["run"]


def test_the_publish_job_checks_out_main_and_downloads_the_experiments_artifact() -> None:
    steps = steps_of("publish")
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "main"
    download = next(s for s in steps if str(s.get("uses", "")).startswith("actions/download"))
    assert download["with"]["name"] == "${{ needs.experiment.outputs.artifact }}"


def test_the_heartbeat_job_runs_only_after_a_successful_run_with_nothing_to_publish() -> None:
    job = daily()["jobs"]["heartbeat"]
    assert job["needs"] == "experiment"
    condition = job["if"]
    assert "needs.experiment.result == 'success'" in condition
    assert "needs.experiment.outputs.changed == 'false'" in condition
    assert "inputs.dry_run != true" in condition
    # never both: publish needs changed == 'true'
    assert "changed == 'true'" in daily()["jobs"]["publish"]["if"]


def test_the_heartbeat_job_can_push_but_never_sees_the_key_or_runs_agent_code() -> None:
    job = daily()["jobs"]["heartbeat"]
    assert job["permissions"] == {"contents": "write"} and "environment" not in job
    text = yaml.safe_dump(job)
    for forbidden in ("ANTHROPIC", "secrets.", "setup_sandbox", "agent-lab", "isolation", "uv "):
        assert forbidden not in text, forbidden


def test_the_heartbeat_job_commits_only_its_log_with_the_configured_identity() -> None:
    steps = steps_of("heartbeat")
    text = "\n".join(s.get("run", "") for s in steps)
    assert re.findall(r"git add .+", text) == ["git add results/heartbeat.log"]
    assert ">> results/heartbeat.log" in text  # appended, never rewritten
    assert "> results/heartbeat.log" not in text.replace(">> results/heartbeat.log", "")
    assert text.count("git push") == 1 and "git pull --rebase" in text and "1 2 3" in text
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "main"
    env = next(s for s in steps if "git commit" in s.get("run", ""))["env"]
    for var in ("GIT_AUTHOR", "GIT_COMMITTER"):
        assert env[f"{var}_NAME"] == "${{ vars.COMMIT_AUTHOR_NAME }}"
        assert env[f"{var}_EMAIL"] == "${{ vars.COMMIT_AUTHOR_EMAIL }}"
    assert "COMMIT_AUTHOR_EMAIL" in steps[0]["env"]["EMAIL"] and "::error::" in steps[0]["run"]


# --- the outcome and the alert -----------------------------------------------------------------


def run_script(step: dict, tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:  # type: ignore[type-arg]
    """Execute a workflow step's shell script for real, with only the given environment."""
    full = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path), **env}
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]], env=full, capture_output=True, text=True, check=False
    )


def expose(tmp_path: Path, outcome: str | None) -> tuple[dict[str, str], str]:
    """Run the 'Expose the outcome' step over an outcome file; returns (outputs, log)."""
    if outcome is not None:
        (tmp_path / "outcome").write_text(outcome)
    github_output = tmp_path / "github_output"
    github_output.write_text("")
    step = next(s for s in steps_of("experiment") if s.get("id") == "outcome")
    done = run_script(step, tmp_path, {"GITHUB_OUTPUT": str(github_output)})
    assert done.returncode == 0, done.stderr
    outputs = dict(line.split("=", 1) for line in github_output.read_text().splitlines())
    return outputs, done.stdout


def test_the_run_step_writes_the_outcome_file_for_real_and_dry_runs() -> None:
    run_step = next(s for s in steps_of("experiment") if "agent-lab run" in s.get("run", ""))
    commands = [line for line in run_step["run"].splitlines() if "agent-lab run" in line]
    assert len(commands) == 2
    assert all('--outcome-file "$RUNNER_TEMP/outcome"' in line for line in commands)


def test_the_workflow_filter_matches_the_characters_the_report_may_write() -> None:
    from agent_lab.runner.outcome import SAFE_VALUE

    step = next(s for s in steps_of("experiment") if s.get("id") == "outcome")
    assert SAFE_VALUE in step["run"]


def test_the_experiment_job_exposes_the_outcome_as_job_outputs() -> None:
    outputs = daily()["jobs"]["experiment"]["outputs"]
    for name in ("kind", "status", "detail", "completed", "planned"):
        assert outputs[name] == f"${{{{ steps.outcome.outputs.{name} }}}}"
    step = next(s for s in steps_of("experiment") if s.get("id") == "outcome")
    assert "!cancelled()" in step["if"]  # also after a failed run step


def test_an_abort_outcome_reaches_the_job_outputs(tmp_path: Path) -> None:
    text = (
        "kind=abort\nstatus=warmup_failed\n"
        "detail=400 BadRequestError: Your credit balance is too low\ncompleted=0\nplanned=32\n"
    )
    outputs, log = expose(tmp_path, text)
    assert outputs == {
        "kind": "abort",
        "status": "warmup_failed",
        "detail": "400 BadRequestError: Your credit balance is too low",
        "completed": "0",
        "planned": "32",
    }
    assert "::warning" not in log


@pytest.mark.parametrize(
    "outcome",
    [
        None,  # the run exited 0 but wrote nothing
        "",
        "garbage\n",
        "status=completed\ndetail=\n",  # no kind
        "kind=exploded\nstatus=completed\n",  # unknown kind
    ],
)
def test_a_missing_or_malformed_outcome_file_is_unknown(
    tmp_path: Path, outcome: str | None
) -> None:
    outputs, _ = expose(tmp_path, outcome)
    assert outputs == {"kind": "unknown", "status": "unknown", "detail": ""}


def test_only_lines_of_the_expected_shape_get_into_the_outputs(tmp_path: Path) -> None:
    hostile = (
        "kind=abort\nstatus=x\ndetail=ok\nEVIL=1\ndetail=a%0Ab\n"
        "detail=has\ttab\nstatus=semi;colon\ndetail=back`tick`\ncompleted=1 2\ndetail=a::b\n"
    )
    outputs, _ = expose(tmp_path, hostile)
    assert set(outputs) <= {"kind", "status", "detail", "completed", "planned"}
    assert outputs["detail"] == "ok" and "EVIL" not in outputs
    assert outputs["status"] == "x" and "completed" not in outputs


def test_a_symlinked_outcome_file_is_not_followed(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("kind=summary\nstatus=completed\ndetail=\n")
    (tmp_path / "outcome").symlink_to(secret)
    github_output = tmp_path / "github_output"
    github_output.write_text("")
    step = next(s for s in steps_of("experiment") if s.get("id") == "outcome")
    run_script(step, tmp_path, {"GITHUB_OUTPUT": str(github_output)})
    assert "kind=unknown" in github_output.read_text()


def test_a_budget_stop_stays_green_but_warns_with_the_counts(tmp_path: Path) -> None:
    outputs, log = expose(
        tmp_path, "kind=summary\nstatus=partial_budget\ndetail=\ncompleted=20\nplanned=32\n"
    )
    assert outputs["status"] == "partial_budget"
    assert log.strip() == (
        "::warning title=Daily run hit the budget cap::completed 20 of 32 attempts "
        "before the daily cost cap stopped the run"
    )


@pytest.mark.parametrize(
    "outcome",
    [
        "kind=summary\nstatus=completed\ndetail=\ncompleted=32\nplanned=32\n",
        "kind=already_done\nstatus=already_done\ndetail=\n",
        "kind=dry_run\nstatus=dry_run\ndetail=\n",
    ],
)
def test_other_outcomes_emit_no_warning(tmp_path: Path, outcome: str) -> None:
    assert "::" not in expose(tmp_path, outcome)[1]


def alert_job() -> dict:  # type: ignore[type-arg]
    return daily()["jobs"]["alert"]


def alert_output(tmp_path: Path, **env: str) -> tuple[int, str]:
    base = dict.fromkeys(
        ("KIND", "STATUS", "DETAIL", "COMPLETED", "PLANNED", "EXPERIMENT", "PUBLISH"), ""
    )
    done = run_script(alert_job()["steps"][0], tmp_path, {**base, **env})
    return done.returncode, done.stdout


def test_the_alert_job_runs_after_experiment_and_publish_even_when_publish_was_skipped() -> None:
    job = alert_job()
    assert job["needs"] == ["experiment", "publish"]
    assert job["if"].startswith("${{ !cancelled() &&")  # evaluated even if publish was skipped
    assert "inputs.dry_run != true" in job["if"]


def test_the_alert_job_fires_on_aborts_tampering_a_missing_outcome_and_a_failed_publish() -> None:
    condition = alert_job()["if"]
    assert "needs.experiment.outputs.kind == 'abort'" in condition
    assert "needs.experiment.outputs.status == 'aborted_tampered'" in condition
    assert (
        "needs.experiment.result == 'success' && needs.experiment.outputs.kind == 'unknown'"
        in condition
    )
    assert "needs.publish.result == 'failure'" in condition
    for green in ("completed", "partial_budget", "already_done"):
        assert green not in condition  # those outcomes never alert


def test_the_alert_job_has_no_token_key_checkout_or_inline_expressions() -> None:
    job = alert_job()
    assert job["permissions"] == {} and "environment" not in job
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == 5
    assert len(job["steps"]) == 1 and "uses" not in job["steps"][0]
    assert "${{" not in job["steps"][0]["run"]  # values come in through env: only
    text = yaml.safe_dump(job)
    for forbidden in ("secrets.", "ANTHROPIC", "checkout", "agent-lab"):
        assert forbidden not in text, forbidden


def test_an_abort_fails_with_its_reason_status_and_message(tmp_path: Path) -> None:
    code, out = alert_output(
        tmp_path,
        KIND="abort",
        STATUS="warmup_failed",
        DETAIL="400 BadRequestError: Your credit balance is too low",
        COMPLETED="0",
        PLANNED="32",
        EXPERIMENT="success",
        PUBLISH="success",
    )
    assert code == 1
    assert out.strip() == (
        "::error title=Daily run aborted::warmup_failed: 400 BadRequestError: Your credit "
        "balance is too low (completed 0 of 32 attempts); the day stays resumable"
    )


def test_tampering_fails_the_run(tmp_path: Path) -> None:
    code, out = alert_output(
        tmp_path, KIND="summary", STATUS="aborted_tampered", EXPERIMENT="success", PUBLISH="success"
    )
    assert code == 1 and out.startswith("::error title=Daily run ended aborted_tampered::")


def test_a_run_that_reports_no_outcome_fails_with_a_clear_message(tmp_path: Path) -> None:
    code, out = alert_output(
        tmp_path, KIND="unknown", STATUS="unknown", EXPERIMENT="success", PUBLISH="skipped"
    )
    assert code == 1
    assert "run finished without reporting an outcome" in out


def test_a_crashed_experiment_job_does_not_add_a_missing_outcome_error(tmp_path: Path) -> None:
    # the failed job is already red; only a publish failure is worth saying
    code, out = alert_output(
        tmp_path, KIND="unknown", STATUS="unknown", EXPERIMENT="failure", PUBLISH="failure"
    )
    assert code == 1 and "without reporting" not in out and "Publishing failed" in out


def test_a_failed_publish_fails_the_run_even_after_a_good_day(tmp_path: Path) -> None:
    code, out = alert_output(
        tmp_path, KIND="summary", STATUS="completed", EXPERIMENT="success", PUBLISH="failure"
    )
    assert code == 1 and out.startswith("::error title=Publishing failed::")


def test_an_abort_and_a_failed_publish_are_both_reported(tmp_path: Path) -> None:
    _, out = alert_output(
        tmp_path, KIND="abort", STATUS="auth_error", EXPERIMENT="success", PUBLISH="failure"
    )
    assert out.count("::error") == 2


def test_hostile_values_cannot_break_out_of_the_alert_script(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    code, out = alert_output(
        tmp_path,
        KIND="abort",
        STATUS="x",
        DETAIL=f"$(touch {marker}) `touch {marker}` ; touch {marker}",
        EXPERIMENT="success",
        PUBLISH="success",
    )
    assert code == 1 and not marker.exists()
    assert "$(touch" in out  # printed literally


# --- pinned runner and actions ---------------------------------------------------------------

WORKFLOWS = {"ci": WORKFLOW, "daily": DAILY}
# Node.js 24 releases. setup-uv publishes no major tags any more, so it is pinned exactly.
ACTION_VERSIONS = {
    "actions/checkout": "v7",
    "actions/upload-artifact": "v7",
    "actions/download-artifact": "v8",
    "astral-sh/setup-uv": "v10.3.3",
}


def test_every_job_in_every_workflow_runs_on_the_pinned_runner_image() -> None:
    for name, path in WORKFLOWS.items():
        for job_name, job in yaml.safe_load(path.read_text())["jobs"].items():
            assert job["runs-on"] == "ubuntu-24.04", f"{name}/{job_name}"


def test_actions_are_on_their_node24_versions_everywhere() -> None:
    seen: set[str] = set()
    for path in WORKFLOWS.values():
        for job in yaml.safe_load(path.read_text())["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    action, _, version = step["uses"].partition("@")
                    assert ACTION_VERSIONS[action] == version, step["uses"]
                    seen.add(action)
    assert seen == set(ACTION_VERSIONS)


# --- the report ---------------------------------------------------------------------------------


def test_ci_checks_that_the_committed_report_is_up_to_date() -> None:
    runs = [s["run"] for s in load()["jobs"]["check"]["steps"] if "run" in s]
    assert "uv run agent-lab report build --check" in runs
    assert runs.index("uv run agent-lab report build --check") > runs.index("uv run mypy scripts/")


def test_the_publish_job_rebuilds_the_report_between_publishing_and_committing() -> None:
    names = [s.get("name", s.get("uses", "")) for s in steps_of("publish")]
    publish = names.index("Validate and copy the results into results/")
    report = names.index("Rebuild the README dashboard and the report")
    commit = names.index("Commit and push")
    assert publish < report < commit
    assert steps_of("publish")[report]["run"] == "uv run agent-lab report build"


def test_the_nothing_new_skip_still_looks_at_results_only() -> None:
    commit = next(s for s in steps_of("publish") if s.get("name") == "Commit and push")["run"]
    assert "git status --porcelain -- results/" in commit and "nothing new to commit" in commit
