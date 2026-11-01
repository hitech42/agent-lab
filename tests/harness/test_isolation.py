import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_lab.harness.isolation import (
    IsolationError,
    LinuxUserIsolation,
    NoIsolation,
    WrappedCommand,
)

SANDBOX_UID, SANDBOX_GID = 4242, 4343


class Recorder:
    """A stand-in for subprocess.run that records argv/env and answers from a function."""

    def __init__(self, answer: Any = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.answer = answer or (lambda argv: (0, ""))

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs.get("env", {})))
        code, out = self.answer(argv)
        return subprocess.CompletedProcess(argv, code, out, "")


def lookup(name: str) -> Any:
    if name != "sbx":
        raise KeyError(name)
    return SimpleNamespace(pw_uid=SANDBOX_UID, pw_gid=SANDBOX_GID)


def make(recorder: Recorder | None = None, harness_uid: int | None = None) -> LinuxUserIsolation:
    return LinuxUserIsolation(
        "sbx",
        runner=recorder or Recorder(),  # type: ignore[arg-type]
        lookup=lookup,
        harness_uid=os.getuid() if harness_uid is None else harness_uid,
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


# --- NoIsolation ----------------------------------------------------------------------


def test_no_isolation_wraps_exactly_like_the_historical_behaviour() -> None:
    wrapped = NoIsolation().wrap("echo hi", {"PATH": "/bin", "HOME": "/h"})
    assert wrapped == WrappedCommand(["/bin/sh", "-c", "echo hi"], {"PATH": "/bin", "HOME": "/h"})


def test_no_isolation_warns_and_the_real_isolation_does_not() -> None:
    warning = NoIsolation().warning
    assert warning is not None and "not isolated" in warning and "ANTHROPIC_API_KEY" in warning
    assert make().warning is None


def test_no_isolation_changes_nothing_on_disk(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_text("x")
    target.chmod(0o600)
    NoIsolation().prepare(tmp_path)
    NoIsolation().share_file(target)
    NoIsolation().cleanup([tmp_path])
    assert mode(target) == 0o600


# --- LinuxUserIsolation: construction -------------------------------------------------


def test_an_unknown_user_is_a_clear_error() -> None:
    with pytest.raises(IsolationError, match=r"does not exist.*setup_sandbox_user"):
        LinuxUserIsolation("nobody-here", lookup=lookup)


@pytest.mark.parametrize("uid", [0, 1000])
def test_the_harness_uid_and_root_are_refused(uid: int) -> None:
    entry = SimpleNamespace(pw_uid=uid, pw_gid=1)
    with pytest.raises(IsolationError, match="kill the harness"):
        LinuxUserIsolation("sbx", lookup=lambda name: entry, harness_uid=1000)


# --- LinuxUserIsolation: wrapping -----------------------------------------------------


def test_wrap_runs_the_command_through_sudo_unshare_setpriv_and_env_i() -> None:
    env = {"PATH": "/venv/bin:/usr/bin", "HOME": "/tmp/h", "LANG": "C.UTF-8"}
    wrapped = make().wrap("echo 'a b' $(x); exit 3", env)
    assert wrapped.argv == [
        "sudo", "-n", "unshare", "--net", "setpriv",
        f"--reuid={SANDBOX_UID}", f"--regid={SANDBOX_GID}", "--clear-groups", "--no-new-privs",
        "env", "-i", "PATH=/venv/bin:/usr/bin", "HOME=/tmp/h", "LANG=C.UTF-8",
        "/bin/sh", "-c", "umask 0; echo 'a b' $(x); exit 3",
    ]  # fmt: skip


def test_the_outer_process_inherits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    wrapped = make().wrap("true", {"PATH": "/bin"})
    assert set(wrapped.env) == {"PATH"}
    assert "sk-ant-secret" not in " ".join(wrapped.argv) + str(wrapped.env)


def test_no_inner_variable_beyond_the_given_ones() -> None:
    argv = make().wrap("true", {"PATH": "/bin", "HOME": "/h"}).argv
    inner = argv[argv.index("-i") + 1 : argv.index("/bin/sh")]
    assert inner == ["PATH=/bin", "HOME=/h"]


# --- kill and cleanup -----------------------------------------------------------------


def test_kill_ends_every_process_of_the_sandbox_user() -> None:
    recorder = Recorder()
    make(recorder).kill(None)  # type: ignore[arg-type]
    assert [argv for argv, _ in recorder.calls] == [
        ["sudo", "-n", "pkill", "-KILL", "-u", str(SANDBOX_UID)]
    ]
    assert set(recorder.calls[0][1]) == {"PATH"}


def test_cleanup_kills_then_reclaims_permissions_as_the_sandbox_user(tmp_path: Path) -> None:
    recorder = Recorder()
    gone = tmp_path / "gone"
    make(recorder).cleanup([tmp_path, gone])  # a directory that no longer exists is skipped
    argvs = [argv for argv, _ in recorder.calls]
    assert argvs[0][:6] == ["sudo", "-n", "pkill", "-KILL", "-u", str(SANDBOX_UID)]
    assert len(argvs) == 2
    assert f"--reuid={SANDBOX_UID}" in argvs[1] and "--net" in argvs[1]
    assert argvs[1][-1] == f"umask 0; chmod -R a+rwX {tmp_path}"
    assert "-L" not in argvs[1][-1].split()  # never follow symlinks


def test_cleanup_skips_a_directory_that_is_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    recorder = Recorder()
    make(recorder).cleanup([link])
    assert len(recorder.calls) == 1  # only the pkill


def test_a_failing_pkill_never_raises() -> None:
    def broken(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError("sudo")

    LinuxUserIsolation("sbx", runner=broken, lookup=lookup, harness_uid=os.getuid()).kill(None)  # type: ignore[arg-type]


# --- prepare and share_file: permissions, never through symlinks ----------------------


def test_prepare_opens_the_tree_and_the_ancestors(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    work = outer / "work"
    (work / "sub").mkdir(parents=True)
    plain, script = work / "plain.txt", work / "sub" / "run.sh"
    plain.write_text("x")
    script.write_text("#!/bin/sh\n")
    outer.chmod(0o700)
    work.chmod(0o700)
    (work / "sub").chmod(0o700)
    plain.chmod(0o600)
    script.chmod(0o700)
    make().prepare(work)
    assert mode(work) == 0o777 and mode(work / "sub") == 0o777
    assert mode(plain) == 0o666  # read/write, not executable
    assert mode(script) == 0o777  # was executable, stays executable
    assert mode(outer) & 0o001  # the other user can traverse to the workspace


def test_prepare_never_follows_a_symlink_to_a_directory_outside(tmp_path: Path) -> None:
    secret = tmp_path / "repo" / "tasks"
    (secret / "hidden_tests").mkdir(parents=True)
    hidden = secret / "hidden_tests" / "test_x.py"
    hidden.write_text("assert 1\n")
    for path in (secret, secret / "hidden_tests", hidden):
        path.chmod(0o700 if path.is_dir() else 0o600)
    work = tmp_path / "work"
    work.mkdir()
    (work / "evil").symlink_to(secret)  # evil -> <repo>/tasks, as an agent could leave it
    (work / "evil_file").symlink_to(hidden)
    (work / "dangling").symlink_to(tmp_path / "missing")
    make().prepare(work)
    assert mode(secret) == 0o700 and mode(secret / "hidden_tests") == 0o700
    assert mode(hidden) == 0o600
    assert (work / "evil").is_symlink() and (work / "evil_file").is_symlink()


def test_prepare_skips_a_directory_that_is_itself_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    target.chmod(0o700)
    (target / "f").write_text("x")
    (target / "f").chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    make().prepare(link)
    assert mode(target) == 0o700 and mode(target / "f") == 0o600


def test_share_file_changes_a_file_but_not_what_a_link_points_to(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_text("x")
    real.chmod(0o600)
    other = tmp_path / "other.txt"
    other.write_text("y")
    other.chmod(0o600)
    link = tmp_path / "link.txt"
    link.symlink_to(other)
    make().share_file(real)
    make().share_file(link)
    assert mode(real) == 0o666
    assert mode(other) == 0o600 and link.is_symlink()


def test_a_symlink_swapped_in_after_the_check_is_refused_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("x")
    target.chmod(0o600)
    link = tmp_path / "swap"
    link.symlink_to(target)
    LinuxUserIsolation._add_mode(link, 0o777)  # O_NOFOLLOW: nothing happens
    assert mode(target) == 0o600


# --- verify ---------------------------------------------------------------------------


def answers(
    sudo: int = 0,
    uid: str = str(SANDBOX_UID),
    routes: str = "0",
    readable: set[str] | None = None,
    writable: set[str] | None = None,
) -> Any:
    def answer(argv: list[str]) -> tuple[int, str]:
        if argv == ["sudo", "-n", "true"]:
            return sudo, ""
        command = argv[-1]
        if command.endswith("id -u"):
            return 0, uid + "\n"
        if "/proc/net/route" in command:
            return 0, routes + "\n"
        for flag, paths in (("-r", readable), ("-x", readable), ("-w", writable)):
            for path in paths or set():
                if command.endswith(f"test {flag} {path}"):
                    return 0, ""
        return 1, ""

    return answer


def verify(recorder: Recorder) -> list[str]:
    return make(recorder).verify([Path("/srv/tasks")], [Path("/srv/site")])


def test_verify_accepts_a_correct_setup() -> None:
    assert verify(Recorder(answers())) == []


def test_verify_reports_missing_sudo() -> None:
    problems = verify(Recorder(answers(sudo=1)))
    assert problems and "sudo" in problems[0]


def test_verify_reports_the_wrong_uid_and_an_open_network() -> None:
    problems = verify(Recorder(answers(uid="1000", routes="3")))
    assert any("uid" in p and "1000" in p for p in problems)
    assert any("network is not isolated" in p and "3 IPv4 routes" in p for p in problems)


def test_verify_ignores_the_tunnel_devices_a_new_namespace_creates() -> None:
    assert verify(Recorder(answers(routes="0"))) == []  # gre0, sit0, ... are not connectivity


def test_verify_reports_readable_hidden_paths_and_writable_read_only_paths() -> None:
    problems = verify(Recorder(answers(readable={"/srv/tasks"}, writable={"/srv/site"})))
    assert any("/srv/tasks is accessible" in p for p in problems)
    assert any("/srv/site is writable" in p for p in problems)


def test_verify_reports_when_the_command_cannot_run_as_the_user() -> None:
    def answer(argv: list[str]) -> tuple[int, str]:
        return (0, "") if argv == ["sudo", "-n", "true"] else (1, "")

    problems = verify(Recorder(answer))
    assert problems and "unshare/setpriv" in problems[0]
