"""Detect changes to the interpreter's site-packages between attempts.

All attempts share one interpreter, so an attempt could poison later grading with
.pth files, edited pytest sources or new modules. The task suite (``tasks/``, hidden
tests and solutions included) is watched too, so an attempt that reaches it is noticed.
This detects such changes; it cannot prevent them (run with --isolation linux-user for that).
"""

import site
import sysconfig
from pathlib import Path

from agent_lab.benchmark.manifest import ChangedFiles, DirectoryManifest


class EnvironmentIntegrity:
    """Manifest of site-packages (and tasks/) directories, captured once and verified later."""

    def __init__(self, site_dirs: list[Path], tasks_dir: Path | None = None) -> None:
        watched = [*site_dirs, *([tasks_dir] if tasks_dir is not None else [])]
        self._site_dirs = [d.resolve() for d in watched]
        self._baseline: dict[Path, DirectoryManifest] | None = None

    @classmethod
    def for_running_interpreter(cls, tasks_dir: Path | None = None) -> EnvironmentIntegrity:
        found = {
            Path(p).resolve()
            for p in (
                sysconfig.get_paths()["purelib"],
                sysconfig.get_paths()["platlib"],
                *site.getsitepackages(),
            )
        }
        return cls(sorted(d for d in found if d.is_dir()), tasks_dir)

    def capture(self) -> None:
        """Record the baseline. Computed once; later calls keep the first baseline."""
        if self._baseline is None:
            self._baseline = self._scan()

    def verify(self) -> ChangedFiles:
        """Added, modified and deleted paths (absolute) since capture()."""
        if self._baseline is None:
            raise RuntimeError("capture() must be called before verify()")
        current = self._scan()
        result = ChangedFiles()
        for site_dir in self._site_dirs:
            changes = self._baseline[site_dir].diff_against(current[site_dir])
            result.added += [str(site_dir / p) for p in changes.added]
            result.modified += [str(site_dir / p) for p in changes.modified]
            result.deleted += [str(site_dir / p) for p in changes.deleted]
        return result

    def _scan(self) -> dict[Path, DirectoryManifest]:
        # Only __pycache__ is ignored: grading runs with -X pycache_prefix, so
        # bytecode caches in site-packages are never trusted by the grader.
        return {d: DirectoryManifest.scan(d, frozenset({"__pycache__"})) for d in self._site_dirs}
