"""Sandbox delegates wrapping, preparation, killing and cleanup to its CommandIsolation."""

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from agent_lab.harness.isolation import CommandIsolation, NoIsolation, WrappedCommand
from agent_lab.harness.sandbox import Sandbox


class RecordingIsolation(CommandIsolation):
    """Runs commands like NoIsolation but records every call, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self._inner = NoIsolation()

    def wrap(self, shell_command: str, env: Mapping[str, str]) -> WrappedCommand:
        self.events.append(("wrap", shell_command))
        return self._inner.wrap(shell_command, env)

    def prepare(self, directory: Path) -> None:
        self.events.append(("prepare", str(directory)))

    def share_file(self, path: Path) -> None:
        self.events.append(("share_file", str(path)))

    def kill(self, proc: subprocess.Popen[bytes]) -> None:
        self.events.append(("kill", ""))
        self._inner.kill(proc)

    def cleanup(self, directories: Sequence[Path]) -> None:
        self.events.append(("cleanup", ",".join(str(d) for d in directories)))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


def test_the_default_isolation_is_none_and_behaves_as_before(tmp_path: Path) -> None:
    with Sandbox(tmp_path) as sandbox:
        assert sandbox.run_command("echo hi", timeout=10).output.strip() == "hi"


def test_root_and_home_are_prepared_when_the_sandbox_is_created(tmp_path: Path) -> None:
    isolation = RecordingIsolation()
    with Sandbox(tmp_path, isolation=isolation):
        prepared = [path for name, path in isolation.events if name == "prepare"]
        assert prepared[0] == str(tmp_path.resolve())
        assert Path(prepared[1]).name.startswith("agent-lab-home-")


def test_every_command_is_wrapped_killed_and_cleaned_up(tmp_path: Path) -> None:
    isolation = RecordingIsolation()
    with Sandbox(tmp_path, isolation=isolation) as sandbox:
        isolation.events.clear()
        sandbox.run_command("true", timeout=10)
        sandbox.run_command("exit 3", timeout=10)
    assert isolation.names()[:4] == ["wrap", "kill", "cleanup", "wrap"]
    assert isolation.names().count("cleanup") == 3  # two commands, then close()


def test_a_timeout_kills_through_the_isolation_and_still_cleans_up(tmp_path: Path) -> None:
    isolation = RecordingIsolation()
    with Sandbox(tmp_path, isolation=isolation, drain_timeout_s=1.0) as sandbox:
        isolation.events.clear()
        result = sandbox.run_command("sleep 30", timeout=0.5)
    assert result.timed_out
    assert isolation.names().count("kill") >= 2  # on timeout and when reaping
    assert "cleanup" in isolation.names()


def test_cleanup_covers_the_root_home_and_registered_directories(tmp_path: Path) -> None:
    isolation = RecordingIsolation()
    root, scratch = tmp_path / "root", tmp_path / "scratch"
    root.mkdir()
    scratch.mkdir()
    with Sandbox(root, isolation=isolation) as sandbox:
        sandbox.prepare_dir(scratch)
        isolation.events.clear()
        sandbox.run_command("true", timeout=10)
    cleaned = next(p for n, p in isolation.events if n == "cleanup").split(",")
    assert str(scratch) in cleaned and str(root.resolve()) in cleaned and len(cleaned) == 3


def test_close_cleans_up_before_the_home_directory_is_removed(tmp_path: Path) -> None:
    isolation = RecordingIsolation()
    sandbox = Sandbox(tmp_path, isolation=isolation)
    home = next(Path(p) for n, p in isolation.events if n == "prepare" and "home" in p)
    seen: list[bool] = []
    original = isolation.cleanup
    isolation.cleanup = lambda dirs: (seen.append(home.exists()), original(dirs))[1]  # type: ignore[method-assign]
    sandbox.close()
    assert seen == [True] and not home.exists()


def test_prepare_dir_goes_to_the_isolation(tmp_path: Path) -> None:
    isolation = RecordingIsolation()
    other = tmp_path / "other"
    other.mkdir()
    with Sandbox(tmp_path, isolation=isolation) as sandbox:
        sandbox.prepare_dir(other)
    assert ("prepare", str(other)) in isolation.events


def test_files_written_by_the_tools_are_shared_with_the_agent(tmp_path: Path) -> None:
    isolation = RecordingIsolation()
    with Sandbox(tmp_path, isolation=isolation) as sandbox:
        sandbox.write_text("sub/a.txt", "x")
    assert ("share_file", str((tmp_path / "sub" / "a.txt").resolve())) in isolation.events


def test_rebased_sandboxes_share_the_isolation_and_prepare_their_root(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    isolation = RecordingIsolation()
    with Sandbox(tmp_path, isolation=isolation) as original, original.rebased(other) as rebased:
        rebased.run_command("true", timeout=10)
    assert ("prepare", str(other.resolve())) in isolation.events
    assert isolation.names().count("wrap") == 1  # the rebased sandbox ran through the same one
