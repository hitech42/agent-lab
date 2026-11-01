"""Where a result came from: commit, lockfile, interpreter and SDK versions."""

import hashlib
import platform
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import anthropic
from pydantic import BaseModel, ConfigDict


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    git_sha: str
    uv_lock_sha256: str
    python_version: str
    anthropic_sdk_version: str
    source: str  # "ci" or "local"


class ProvenanceReader:
    """Collects provenance once per run."""

    def __init__(
        self,
        repo_root: Path,
        environ: Mapping[str, str],
        git_sha_lookup: Callable[[Path], str | None] | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._environ = environ
        self._git_sha_lookup = git_sha_lookup or self._git_rev_parse

    def read(self) -> Provenance:
        sha = self._environ.get("GITHUB_SHA") or self._git_sha_lookup(self._repo_root)
        return Provenance(
            git_sha=sha or "unknown",
            uv_lock_sha256=self._lock_sha(),
            python_version=platform.python_version(),
            anthropic_sdk_version=anthropic.__version__,
            source="ci" if self._environ.get("GITHUB_ACTIONS") == "true" else "local",
        )

    def _lock_sha(self) -> str:
        lock = self._repo_root / "uv.lock"
        if not lock.is_file():
            return "unknown"
        return hashlib.sha256(lock.read_bytes()).hexdigest()

    @staticmethod
    def _git_rev_parse(repo_root: Path) -> str | None:
        try:
            done = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except OSError, subprocess.SubprocessError:
            return None
        return done.stdout.strip() or None if done.returncode == 0 else None
