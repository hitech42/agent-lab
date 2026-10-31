"""Checks applied to every grade, regardless of the grading kind."""

import os
from pathlib import Path, PurePosixPath

from agent_lab.benchmark.manifest import ChangedFiles


class ProtectedFilesCheck:
    """Flags any added, modified or deleted file that matches a protected glob."""

    def evaluate(self, globs: list[str], changes: ChangedFiles) -> list[str]:
        return [
            path
            for path in sorted({*changes.added, *changes.modified, *changes.deleted})
            if any(PurePosixPath(path).full_match(glob) for glob in globs)
        ]


class CanaryCheck:
    """Flags canary paths that exist (they must not exist after the attempt)."""

    def evaluate(self, canaries: list[str], root: Path) -> list[str]:
        return [path for path in canaries if os.path.lexists(root / path)]
