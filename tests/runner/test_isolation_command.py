"""`agent-lab run --isolation ...`: warning, verification, fail-closed CI, wiring."""

from pathlib import Path
from typing import Any

import pytest
from runner_support import MONDAY, build_cli

from agent_lab.harness.isolation import (
    CommandIsolation,
    IsolationError,
    NoIsolation,
)
from agent_lab.runner.run_command import RunArgs, make_isolation


class FakeLinuxIsolation(NoIsolation):
    """Runs like NoIsolation, but claims to be the real thing and reports chosen problems."""

    warning = None

    def __init__(self, problems: list[str] | None = None) -> None:
        self.problems = problems or []
        self.verified: list[tuple[list[Path], list[Path]]] = []
        self.prepared: list[Path] = []

    def verify(self, hidden: Any, read_only: Any) -> list[str]:
        self.verified.append((list(hidden), list(read_only)))
        return self.problems

    def prepare(self, directory: Path) -> None:
        self.prepared.append(directory)


def test_a_real_run_without_isolation_warns_exactly_once(tmp_path: Path) -> None:
    code, output = build_cli(tmp_path).run("--date", MONDAY, "--trials", "1")
    assert code == 0 and output.count("agent commands are not isolated") == 1
    assert output.index("warning: agent commands") < output.index("completed")


def test_a_dry_run_prints_the_isolation_but_no_warning(tmp_path: Path) -> None:
    code, output = build_cli(tmp_path, api_key=None).run("--date", MONDAY, "--dry-run")
    assert code == 0 and "isolation: none" in output and "not isolated" not in output
    _, linux = build_cli(tmp_path, api_key=None).run(
        "--date", MONDAY, "--dry-run", "--isolation", "linux-user"
    )
    assert "isolation: linux-user" in linux


def test_a_verified_isolation_runs_without_a_warning_and_is_used(tmp_path: Path) -> None:
    fake = FakeLinuxIsolation()
    world = build_cli(tmp_path, isolation=lambda args: fake)
    code, output = world.run("--date", MONDAY, "--trials", "1", "--isolation", "linux-user")
    assert code == 0 and "not isolated" not in output
    hidden, read_only = fake.verified[0]
    assert TASKS_DIR_NAME in {p.name for p in hidden} and ".git" in {p.name for p in hidden}
    assert read_only and read_only[0].name == "site-packages"
    assert len(fake.prepared) >= 12  # the sandboxes of the run were built with this isolation


TASKS_DIR_NAME = "tasks"


def test_failed_verification_exits_one_before_any_api_call(tmp_path: Path) -> None:
    fake = FakeLinuxIsolation(["passwordless `sudo -n` does not work", "tasks is accessible"])
    world = build_cli(tmp_path, isolation=lambda args: fake)
    code, output = world.run("--date", MONDAY, "--trials", "1", "--isolation", "linux-user")
    assert code == 1 and "isolation is not usable" in output
    assert "sudo -n" in output and "tasks is accessible" in output
    assert world.client.requests == [] and not (tmp_path / "results").exists()


def test_an_unusable_isolation_choice_is_a_clear_error(tmp_path: Path) -> None:
    def refuse(args: RunArgs) -> CommandIsolation:
        raise IsolationError("user 'x' does not exist")

    world = build_cli(tmp_path, isolation=refuse)
    code, output = world.run("--date", MONDAY, "--isolation", "linux-user")
    assert code == 1 and "does not exist" in output and world.client.requests == []


def test_the_isolation_flags_reach_the_factory(tmp_path: Path) -> None:
    seen: list[RunArgs] = []

    def spy(args: RunArgs) -> CommandIsolation:
        seen.append(args)
        return NoIsolation()

    world = build_cli(tmp_path, isolation=spy)
    world.run("--date", MONDAY, "--trials", "1", "--tasks", "fix-date-range")
    world.run("--date", "2026-11-03", "--trials", "1", "--tasks", "fix-date-range",
              "--isolation", "none", "--isolation-user", "someone")  # fmt: skip
    assert [(a.isolation, a.isolation_user) for a in seen] == [
        ("none", "agentlab-sandbox"),
        ("none", "someone"),
    ]


def test_an_unknown_isolation_name_is_rejected_by_the_parser(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_cli(tmp_path).run("--date", MONDAY, "--isolation", "docker")


def test_the_default_factory_builds_the_requested_isolation() -> None:
    assert isinstance(make_isolation(RunArgs()), NoIsolation)
    with pytest.raises(IsolationError, match="does not exist"):
        make_isolation(RunArgs(isolation="linux-user", isolation_user="no-such-user-xyz"))


# --- CI fails closed ------------------------------------------------------------------


def test_a_real_ci_run_without_isolation_is_refused_before_any_api_call(tmp_path: Path) -> None:
    world = build_cli(tmp_path, extra_environ={"GITHUB_ACTIONS": "true"})
    code, output = world.run("--date", MONDAY, "--trials", "1")
    assert code == 1 and "CI runs require --isolation linux-user" in output
    code, output = world.run("--date", MONDAY, "--trials", "1", "--isolation", "none")
    assert code == 1 and "CI runs require --isolation linux-user" in output
    assert world.client.requests == [] and not (tmp_path / "results").exists()


def test_ci_with_linux_user_isolation_runs(tmp_path: Path) -> None:
    world = build_cli(
        tmp_path,
        extra_environ={"GITHUB_ACTIONS": "true"},
        isolation=lambda args: FakeLinuxIsolation(),
    )
    code, _ = world.run("--date", MONDAY, "--trials", "1", "--isolation", "linux-user")
    assert code == 0 and world.client.requests


def test_a_ci_dry_run_needs_no_isolation(tmp_path: Path) -> None:
    world = build_cli(tmp_path, api_key=None, extra_environ={"GITHUB_ACTIONS": "true"})
    code, output = world.run("--date", MONDAY, "--dry-run")
    assert code == 0 and "isolation: none" in output


def test_other_ci_values_do_not_trigger_the_check(tmp_path: Path) -> None:
    world = build_cli(tmp_path, extra_environ={"GITHUB_ACTIONS": "false", "CI": "true"})
    code, _ = world.run("--date", MONDAY, "--trials", "1")
    assert code == 0
