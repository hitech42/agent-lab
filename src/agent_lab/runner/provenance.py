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
    git_dirty: bool | None  # uncommitted changes outside results/; None if git is unavailable
    uv_lock_sha256: str
    python_version: str
    anthropic_sdk_version: str
    source: str  # "ci" or "local"
    runner_image: str | None = None  # e.g. "ubuntu24/20251020.1" on hosted runners


class ProvenanceReader:
    """Collects provenance once per run."""

    def __init__(
        self,
        repo_root: Path,
        environ: Mapping[str, str],
        git_sha_lookup: Callable[[Path], str | None] | None = None,
        git_dirty_lookup: Callable[[Path], bool | None] | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._environ = environ
        self._git_sha_lookup = git_sha_lookup or self._git_rev_parse
        self._git_dirty_lookup = git_dirty_lookup or self._git_status_dirty

    def read(self) -> Provenance:
        sha = self._environ.get("GITHUB_SHA") or self._git_sha_lookup(self._repo_root)
        return Provenance(
            git_sha=sha or "unknown",
            git_dirty=self._git_dirty_lookup(self._repo_root),
            uv_lock_sha256=self._lock_sha(),
            python_version=platform.python_version(),
            anthropic_sdk_version=anthropic.__version__,
            source="ci" if self._environ.get("GITHUB_ACTIONS") == "true" else "local",
            runner_image=self._runner_image(),
        )

    def _runner_image(self) -> str | None:
        """The hosted runner image (ImageOS/ImageVersion), when those variables are set."""
        parts = [self._environ.get(name) for name in ("ImageOS", "ImageVersion")]
        found = [p for p in parts if p]
        return "/".join(found) if found else None

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

    @staticmethod
    def _git_status_dirty(repo_root: Path) -> bool | None:
        """True if tracked or untracked (not ignored) files differ, ignoring results/.

        results/ is excluded so that a day's own earlier output does not mark a resumed
        or CI run as dirty. None when git cannot answer.
        """
        try:
            done = subprocess.run(
                ["git", "status", "--porcelain", "--", ".", ":(exclude)results"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except OSError, subprocess.SubprocessError:
            return None
        return bool(done.stdout.strip()) if done.returncode == 0 else None
