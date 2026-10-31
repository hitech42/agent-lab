"""Directory manifests: detect added, modified and deleted files without git."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


class ChangedFiles(BaseModel):
    """Relative POSIX paths that differ between two manifests (data only)."""

    added: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ManifestEntry:
    kind: Literal["file", "symlink", "special", "unreadable"]
    size: int
    sha256: str


class DirectoryManifest:
    """Relative path -> (kind, size, sha256) for every file under a root.

    Symlinks are recorded by target and never followed, so scanning cannot read
    outside the root. Directories named in ``ignore_dirs`` are skipped.
    """

    def __init__(self, entries: dict[str, ManifestEntry]) -> None:
        self._entries = dict(entries)

    @classmethod
    def scan(cls, root: Path, ignore_dirs: frozenset[str] = CACHE_DIRS) -> DirectoryManifest:
        entries: dict[str, ManifestEntry] = {}
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            descend: list[str] = []
            for name in sorted(dirnames):
                path = base / name
                if name in ignore_dirs:
                    continue
                if path.is_symlink():
                    entries[cls._rel(root, path)] = cls._entry(path)
                else:
                    descend.append(name)
            dirnames[:] = descend
            for name in sorted(filenames):
                path = base / name
                entries[cls._rel(root, path)] = cls._entry(path)
        return cls(entries)

    def diff_against(self, current: DirectoryManifest) -> ChangedFiles:
        """What changed going from this (baseline) manifest to ``current``."""
        before, after = self._entries, current._entries
        return ChangedFiles(
            added=sorted(set(after) - set(before)),
            modified=sorted(p for p in before.keys() & after.keys() if before[p] != after[p]),
            deleted=sorted(set(before) - set(after)),
        )

    @staticmethod
    def _rel(root: Path, path: Path) -> str:
        return path.relative_to(root).as_posix()

    @staticmethod
    def _entry(path: Path) -> ManifestEntry:
        try:
            if path.is_symlink():
                target = os.readlink(path)
                return ManifestEntry("symlink", 0, hashlib.sha256(target.encode()).hexdigest())
            if not path.is_file():
                return ManifestEntry("special", 0, "")
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            return ManifestEntry("file", path.stat().st_size, digest)
        except OSError:
            return ManifestEntry("unreadable", 0, "")
