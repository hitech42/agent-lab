import contextlib
import os
import signal
import time
from pathlib import Path

import pytest

from agent_lab.harness.sandbox import Sandbox, SandboxError


@pytest.mark.parametrize("bad", ["../outside.txt", "a/../../outside.txt", "/etc/passwd"])
def test_rejects_path_escapes(sandbox: Sandbox, bad: str) -> None:
    with pytest.raises(SandboxError):
        sandbox.read_text(bad)
    with pytest.raises(SandboxError):
        sandbox.write_text(bad, "x")


def test_rejects_symlink_to_outside_file(
    sandbox: Sandbox, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("secret")
    (sandbox.root / "link.txt").symlink_to(outside)
    with pytest.raises(SandboxError):
        sandbox.read_text("link.txt")
    with pytest.raises(SandboxError):
        sandbox.write_text("link.txt", "overwrite")
    assert outside.read_text() == "secret"


def test_rejects_symlinked_directory_and_new_file_through_it(
    sandbox: Sandbox, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside_dir")
    (sandbox.root / "d").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxError):
        sandbox.write_text("d/new.txt", "x")
    with pytest.raises(SandboxError):
        sandbox.list_files("d")
    assert list(outside.iterdir()) == []


def test_allows_inside_paths(sandbox: Sandbox) -> None:
    sandbox.write_text("sub/a.txt", "hi")
    (sandbox.root / "alias.txt").symlink_to(sandbox.root / "sub" / "a.txt")
    assert sandbox.read_text("sub/a.txt") == "hi"
    assert sandbox.read_text("alias.txt") == "hi"
    assert sandbox.read_text(str(sandbox.root / "sub" / "a.txt")) == "hi"
    assert sandbox.read_text("sub/../sub/a.txt") == "hi"
    assert "sub/a.txt" in sandbox.list_files()


def test_minimal_env_has_exactly_three_vars(tmp_path: Path) -> None:
    env = Sandbox.minimal_env(tmp_path)
    assert set(env) == {"PATH", "HOME", "LANG"}
    assert env["HOME"] == str(tmp_path)


def test_subprocess_cannot_see_api_key_or_other_env(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret-value")
    monkeypatch.setenv("SOME_OTHER_SECRET", "hunter2")
    result = sandbox.run_command("env", timeout=10)

    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY" not in result.output
    assert "sk-ant-test-secret-value" not in result.output
    assert "SOME_OTHER_SECRET" not in result.output and "hunter2" not in result.output
    names = {line.split("=", 1)[0] for line in result.output.splitlines() if "=" in line}
    assert {"PATH", "HOME", "LANG"} <= names
    # only the explicit vars plus what the shell itself adds
    assert names <= {"PATH", "HOME", "LANG", "PWD", "OLDPWD", "SHLVL", "_"}


def test_cwd_is_root_and_python_available(sandbox: Sandbox) -> None:
    result = sandbox.run_command("pwd && python -c 'print(1+1)'", timeout=30)
    assert result.output.splitlines()[0] == str(sandbox.root)
    assert result.output.splitlines()[1] == "2"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_kills_whole_process_group(sandbox: Sandbox) -> None:
    started = time.monotonic()
    result = sandbox.run_command("sleep 300 & echo $! > child.pid; sleep 300", timeout=1)

    assert time.monotonic() - started < 10
    assert result.timed_out and result.exit_code is None
    assert "timed out after 1s" in result.output
    child = int((sandbox.root / "child.pid").read_text())
    deadline = time.monotonic() + 3
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child)


def test_nonzero_exit_reported(sandbox: Sandbox) -> None:
    result = sandbox.run_command("echo hi; exit 3", timeout=10)
    assert result.exit_code == 3 and not result.timed_out and "hi" in result.output


def test_unbounded_output_is_capped(sandbox: Sandbox) -> None:
    started = time.monotonic()
    result = sandbox.run_command("yes", timeout=1)

    assert time.monotonic() - started < 8
    assert result.timed_out
    assert len(result.output) < 1_000_000 + 500  # the default cap
    assert "output truncated" in result.output and "bytes discarded" in result.output
    assert "timed out after 1s" in result.output
    assert result.output.startswith("y\ny\n")  # the FIRST bytes are kept


def test_output_cap_marker_counts_discarded_bytes(tmp_path: Path) -> None:
    with Sandbox(tmp_path, output_cap_bytes=100) as small:
        result = small.run_command("python -c \"print('a' * 1000)\"", timeout=10)

    assert not result.timed_out and result.exit_code == 0
    assert result.output.startswith("a" * 100 + "\n[... output truncated, ")
    assert "901 bytes discarded" in result.output  # 1000 'a' + newline - 100 kept


# `setsid` is not available on macOS, so escape the process group from Python instead.
_ESCAPE = (
    'python -c "import subprocess; '
    "p = subprocess.Popen(['sleep', '100'], start_new_session=True); "
    "open('escaped.pid', 'w').write(str(p.pid))\""
)


def _kill_escaped(sandbox: Sandbox) -> None:
    pid_file = sandbox.root / "escaped.pid"
    if pid_file.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_escaped_process_holding_pipe_does_not_block(sandbox: Sandbox) -> None:
    try:
        started = time.monotonic()
        result = sandbox.run_command(f"{_ESCAPE}; echo started", timeout=2)
        elapsed = time.monotonic() - started

        assert (sandbox.root / "escaped.pid").exists()  # the escape really happened
        assert elapsed < 2 + 6
        assert "started" in result.output
    finally:
        _kill_escaped(sandbox)


def test_escaped_process_after_timeout_is_bounded(tmp_path: Path) -> None:
    with Sandbox(tmp_path, drain_timeout_s=1.0) as quick:
        try:
            started = time.monotonic()
            result = quick.run_command(f"{_ESCAPE}; sleep 60", timeout=1)

            assert (quick.root / "escaped.pid").exists()
            assert time.monotonic() - started < 1 + 1 + 3
            assert result.timed_out and "timed out" in result.output
        finally:
            _kill_escaped(quick)
