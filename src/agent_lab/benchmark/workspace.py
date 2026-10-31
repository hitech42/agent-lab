"""A throwaway copy of a task's project that the agent works in."""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from types import TracebackType

from agent_lab.benchmark.manifest import CACHE_DIRS, ChangedFiles, DirectoryManifest
from agent_lab.benchmark.task import Task


class TaskWorkspace:
    """Owns a temp directory holding a copy of project/ plus its baseline manifest.

    No .git is created. ``diff()`` compares the directory's current contents with
    the baseline recorded when the workspace was created.
    """

    def __init__(self, root: Path, baseline: DirectoryManifest) -> None:
        self.root = root
        self._baseline = baseline

    @classmethod
    def create(cls, task: Task, base_dir: Path) -> TaskWorkspace:
        root = Path(tempfile.mkdtemp(prefix=f"task-{task.id}-", dir=base_dir)).resolve()
        cls._copy_tree(task.directory / Task.PROJECT, root)
        return cls(root, DirectoryManifest.scan(root))

    def snapshot(self, base_dir: Path | None = None) -> TaskWorkspace:
        """Copy the current contents to a fresh directory, keeping the same baseline.

        Grading operates on the snapshot, so anything still running against the
        original workspace cannot affect the result.
        """
        root = Path(tempfile.mkdtemp(prefix="grade-snapshot-", dir=base_dir)).resolve()
        self._copy_tree(self.root, root)
        return TaskWorkspace(root, self._baseline)

    def diff(self) -> ChangedFiles:
        return self._baseline.diff_against(DirectoryManifest.scan(self.root))

    def overlay(self, files_dir: Path) -> None:
        """Copy every file under files_dir over the workspace (used to apply solution/)."""
        self._copy_tree(files_dir, self.root)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> TaskWorkspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @staticmethod
    def _copy_tree(src: Path, dst: Path) -> None:
        """Copy regular files and symlinks (as links); skip caches and special files."""
        for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
            base = Path(dirpath)
            target_dir = dst / base.relative_to(src)
            target_dir.mkdir(parents=True, exist_ok=True)
            kept: list[str] = []
            for name in dirnames:
                path = base / name
                if name in CACHE_DIRS:
                    continue
                if path.is_symlink():
                    TaskWorkspace._copy_symlink(path, target_dir / name)
                else:
                    kept.append(name)
            dirnames[:] = kept
            for name in filenames:
                path = base / name
                if path.is_symlink():
                    TaskWorkspace._copy_symlink(path, target_dir / name)
                elif path.is_file():
                    with contextlib.suppress(OSError):
                        shutil.copyfile(path, target_dir / name)

    @staticmethod
    def _copy_symlink(src: Path, dst: Path) -> None:
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        os.symlink(os.readlink(src), dst)
