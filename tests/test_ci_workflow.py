"""The CI workflow keeps the guarantees we rely on (cheap, no network, no secrets)."""

import re
from pathlib import Path

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


def test_daily_runs_a_primary_and_a_backup_at_non_round_minutes() -> None:
    crons = [entry["cron"] for entry in daily()[True]["schedule"]]
    assert len(crons) == 2
    for cron in crons:
        minute, hour, *rest = cron.split()
        assert rest == ["*", "*", "*"] and minute not in {"0", "30", "15", "45"}
        assert minute.isdigit() and hour.isdigit()
    assert len({c.split()[1] for c in crons}) == 2  # different hours, same UTC day


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
    assert re.findall(r"git add \S+", text) == ["git add results/"]
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
