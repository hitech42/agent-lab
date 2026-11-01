"""Integration tests for LinuxUserIsolation, asserted from inside the sandbox.

They need Linux, passwordless sudo, unshare/setpriv and the sandbox user created by
scripts/setup_sandbox_user.sh (with the uv layout described there). Everywhere else they
are skipped. Run them with `uv run pytest -m isolation`; CI does on every push.
"""

import os
import pwd
import shutil
import socket
import subprocess
import sys
import sysconfig
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.isolation import IsolationError, LinuxUserIsolation, NoIsolation
from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools.exec_tools import RunTestsTool

REPO = Path(__file__).resolve().parents[2]
USER = os.environ.get("AGENT_LAB_SANDBOX_USER", LinuxUserIsolation.DEFAULT_USER)


def unsupported_reason() -> str | None:
    """Why these tests cannot run here, or None. Path checks are left to the tests."""
    if not sys.platform.startswith("linux"):
        return "needs Linux"
    try:
        isolation = LinuxUserIsolation(USER)
    except IsolationError as exc:
        return str(exc)
    problems = isolation.verify([], [])
    return "; ".join(problems) if problems else None


_REASON = unsupported_reason()
pytestmark = [
    pytest.mark.isolation,
    pytest.mark.slow,
    pytest.mark.skipif(_REASON is not None, reason=f"sandbox isolation unavailable: {_REASON}"),
]


@pytest.fixture
def isolation() -> LinuxUserIsolation:
    return LinuxUserIsolation(USER)


@pytest.fixture
def sandbox(tmp_path: Path, isolation: LinuxUserIsolation) -> Iterator[Sandbox]:
    root = tmp_path / "work"
    root.mkdir()
    with Sandbox(root, isolation=isolation) as sb:
        yield sb


def run(sandbox: Sandbox, command: str, timeout: float = 30) -> tuple[int | None, str]:
    result = sandbox.run_command(command, timeout)
    return result.exit_code, result.output


# --- the reproduced leaks -------------------------------------------------------------


def test_the_parents_environment_is_not_readable(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output = run(sandbox, "cat /proc/$PPID/environ")
    assert code != 0 or "ANTHROPIC_API_KEY" not in output


def test_another_process_environment_with_the_key_is_unreadable(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    """A control: the very same read works without isolation and fails with it."""
    key = "sk-ant-integration-control-key"
    holder = subprocess.Popen(["sleep", "60"], env={"ANTHROPIC_API_KEY": key})
    try:
        command = f"cat /proc/{holder.pid}/environ"
        (tmp_path / "open").mkdir()
        (tmp_path / "closed").mkdir()
        with Sandbox(tmp_path / "open", isolation=NoIsolation()) as unisolated:
            assert key in run(unisolated, command)[1]  # the leak is real without isolation
        with Sandbox(tmp_path / "closed", isolation=isolation) as isolated:
            code, output = run(isolated, command)
        assert code != 0 and key not in output
        assert "ANTHROPIC_API_KEY" not in output
    finally:
        holder.kill()
        holder.wait()


def test_hidden_tests_and_solutions_are_unreadable(sandbox: Sandbox) -> None:
    for target in (
        REPO / "tasks" / "fix-date-range" / "hidden_tests",
        REPO / "tasks" / "fix-date-range" / "solution",
        REPO / "tasks" / "fix-date-range" / "task.yaml",
    ):
        _, output = run(sandbox, f"ls {target}; cat {target}/* {target} 2>&1")
        assert "def test" not in output and "expected_answer" not in output, target
    assert run(sandbox, f"ls {REPO / 'tasks'}")[0] != 0
    assert run(sandbox, f"cat {REPO / 'tasks' / 'fix-date-range' / 'task.yaml'}")[0] != 0
    assert run(sandbox, f"cat {REPO / '.git' / 'config'}")[0] != 0


def test_the_home_the_repo_and_the_runner_temp_cannot_be_listed(sandbox: Sandbox) -> None:
    targets = [Path.home(), REPO]
    if os.environ.get("RUNNER_TEMP"):
        targets.append(Path(os.environ["RUNNER_TEMP"]))
    for target in targets:
        code, output = run(sandbox, f"ls {target}")
        assert code != 0, f"{target} can be listed by the sandbox user:\n{output}"


def test_site_packages_are_not_writable_but_the_venv_runs(sandbox: Sandbox) -> None:
    purelib = Path(sysconfig.get_paths()["purelib"])
    code, _ = run(sandbox, f"echo 'import os' > {purelib}/agent_lab_probe.pth")
    assert code != 0 and not (purelib / "agent_lab_probe.pth").exists()
    assert run(sandbox, f"echo x >> {purelib}/pytest/__init__.py")[0] != 0
    code, output = run(sandbox, "python -c 'import pytest, sys; print(pytest.__name__)'")
    assert code == 0 and "pytest" in output


def test_an_outbound_connection_fails_while_loopback_works_without_isolation(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    connect = (
        f"python -c \"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=3)\""
    )
    outside = "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)\""
    try:
        (tmp_path / "open").mkdir()
        (tmp_path / "closed").mkdir()
        with Sandbox(tmp_path / "open", isolation=NoIsolation()) as unisolated:
            assert run(unisolated, connect)[0] == 0  # reachable without isolation
        with Sandbox(tmp_path / "closed", isolation=isolation) as isolated:
            assert run(isolated, connect)[0] != 0
            code, output = run(isolated, outside)
            assert code != 0 and "Traceback" in output
    finally:
        server.close()


# --- processes ------------------------------------------------------------------------


def sandbox_pids(user: str, name: str) -> list[str]:
    """Live processes of the sandbox user called ``name`` (killed-but-unreaped zombies and
    processes that exited do not count: only an init that never reaps keeps those around)."""
    uid = pwd.getpwnam(user).pw_uid
    done = subprocess.run(
        ["ps", "-u", str(uid), "-o", "pid=,stat=,comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    rows = [line.split(None, 2) for line in done.stdout.splitlines() if line.strip()]
    return [pid for pid, state, comm in rows if comm == name and not state.startswith("Z")]


def test_a_setsid_background_process_is_gone_after_the_command(sandbox: Sandbox) -> None:
    run(sandbox, "setsid sleep 300 >/dev/null 2>&1 &")
    time.sleep(0.2)
    assert sandbox_pids(USER, "sleep") == []


def test_a_process_that_outlives_the_shell_is_gone_when_the_sandbox_closes(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    (tmp_path / "w").mkdir()
    with Sandbox(tmp_path / "w", isolation=isolation, drain_timeout_s=1.0) as sb:
        result = sb.run_command("nohup sleep 301 >/dev/null 2>&1 & sleep 60", timeout=1)
        assert result.timed_out
    assert sandbox_pids(USER, "sleep") == []


# --- files the harness and the agent share --------------------------------------------


def test_files_the_agent_makes_stay_readable_and_deletable_for_the_harness(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    (root / "project.py").write_text("x = 1\n")  # written by the harness
    with Sandbox(root, isolation=isolation) as sb:
        sb.write_text("tool.txt", "from the tool\n")
        code, _ = run(
            sb,
            "echo 'edited' >> project.py && echo 'edited' >> tool.txt && mkdir -p a/b && "
            "echo secret > a/b/f && chmod 000 a/b/f && chmod 000 a/b && chmod 000 a && "
            "umask 077 && echo private > private.txt",
        )
        assert code == 0
        assert "edited" in (root / "project.py").read_text()
        assert "edited" in sb.read_text("tool.txt")
        assert (root / "a" / "b" / "f").read_text().strip() == "secret"  # reclaimed
        assert (root / "private.txt").read_text().strip() == "private"
    shutil.rmtree(root)  # the harness can delete everything the agent made
    assert not root.exists()


def test_a_symlink_the_agent_leaves_never_exposes_the_repo_to_the_sandbox_user(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    before = {p: p.stat().st_mode for p in (REPO / "tasks", REPO / ".git", REPO)}
    with Sandbox(root, isolation=isolation) as sb:
        run(sb, f"ln -s {REPO / 'tasks'} evil && ln -s {REPO / '.git'} evil_git")
        snapshot = tmp_path / "snapshot"
        shutil.copytree(root, snapshot, symlinks=True)
        with sb.rebased(snapshot) as snap:  # as grading does, on a copy holding the links
            code, output = run(snap, "ls evil; cat evil/fix-date-range/task.yaml")
            assert code != 0 and "expected_answer" not in output
            assert run(snap, "cat evil_git/config")[0] != 0
        sb.prepare_dir(root)  # and preparing again must not follow them either
    assert {p: p.stat().st_mode for p in before} == before


# --- the real thing still works -------------------------------------------------------


def solved_workspace(tmp_path: Path, solved: bool) -> tuple[Task, TaskWorkspace]:
    task = TaskSuite.load(REPO / "tasks").get("fix-date-range")
    workspace = TaskWorkspace.create(task, tmp_path)
    if solved:
        workspace.overlay(task.directory / Task.SOLUTION)
    return task, workspace


def test_run_tests_and_hidden_test_grading_pass_for_the_real_solution(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    task, workspace = solved_workspace(tmp_path, solved=True)
    with workspace, Sandbox(workspace.root, isolation=isolation) as sandbox:
        visible = RunTestsTool().execute(sandbox, {})
        assert "passed" in visible and "[exit code: 0]" in visible
        grade = Grader.for_task(task).grade(task, workspace, sandbox, "")
    assert grade.passed and grade.tests_total > 0 and grade.tests_passed == grade.tests_total
    assert not grade.protected_violations and not grade.environment_tampered


def test_grading_still_fails_the_untouched_project(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    task, workspace = solved_workspace(tmp_path, solved=False)
    with workspace, Sandbox(workspace.root, isolation=isolation) as sandbox:
        grade = Grader.for_task(task).grade(task, workspace, sandbox, "")
    assert not grade.passed and grade.tests_failed > 0


def test_grading_leaves_no_files_the_harness_cannot_remove(
    tmp_path: Path, isolation: LinuxUserIsolation
) -> None:
    task, workspace = solved_workspace(tmp_path, solved=True)
    with workspace, Sandbox(workspace.root, isolation=isolation) as sandbox:
        Grader.for_task(task).grade(task, workspace, sandbox, "")
    leftovers = [p.name for p in Path(tempfile_dir()).glob("agent-lab-pytest-*")]
    assert leftovers == []


def tempfile_dir() -> str:
    import tempfile

    return tempfile.gettempdir()
