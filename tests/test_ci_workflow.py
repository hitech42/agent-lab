"""The CI workflow keeps the guarantees we rely on (cheap, no network, no secrets)."""

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
