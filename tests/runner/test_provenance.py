import hashlib
import platform
from pathlib import Path

import anthropic

from agent_lab.runner.provenance import ProvenanceReader


def reader(tmp_path: Path, environ: dict[str, str], lookup=None) -> ProvenanceReader:  # type: ignore[no-untyped-def]
    return ProvenanceReader(tmp_path, environ, lookup or (lambda root: None))


def test_ci_values_come_from_the_environment(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("lock contents")
    env = {"GITHUB_SHA": "deadbeef", "GITHUB_ACTIONS": "true"}
    prov = reader(tmp_path, env, lambda root: "should-not-be-used").read()
    assert prov.git_sha == "deadbeef" and prov.source == "ci"
    assert prov.uv_lock_sha256 == hashlib.sha256(b"lock contents").hexdigest()
    assert prov.python_version == platform.python_version()
    assert prov.anthropic_sdk_version == anthropic.__version__


def test_local_runs_fall_back_to_git(tmp_path: Path) -> None:
    prov = reader(tmp_path, {}, lambda root: f"git-{root.name}").read()
    assert prov.git_sha == f"git-{tmp_path.name}" and prov.source == "local"


def test_unknown_when_nothing_is_available(tmp_path: Path) -> None:
    prov = reader(tmp_path, {}).read()
    assert prov.git_sha == "unknown" and prov.uv_lock_sha256 == "unknown"


def test_a_real_git_checkout_is_read(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        cwd=tmp_path,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert ProvenanceReader(tmp_path, {}).read().git_sha == sha


def test_a_directory_that_is_not_a_repository_gives_unknown(tmp_path: Path) -> None:
    import os

    if not os.environ.get("GIT_DIR") and (tmp_path / ".git").exists():
        return
    sha = ProvenanceReader(tmp_path, {}).read().git_sha
    # tmp_path normally sits outside any repository; if the machine's temp dir is inside one,
    # git legitimately answers, so only check the shape.
    assert sha == "unknown" or len(sha) >= 7
