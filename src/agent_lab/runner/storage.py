"""Append-only results storage: JSONL attempts, run summaries, abort events, transcripts."""

import gzip
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_lab.runner.records import AbortRecord, AttemptRecord, RunSummary


class ResultsStore:
    """Reads and writes results/runs/. Files are only ever appended to or created.

    Nothing here deletes, truncates or rewrites a result file: attempts are appended
    to <date>.jsonl, the summary and abort files are created exclusively.
    """

    def __init__(self, results_dir: Path) -> None:
        self.runs_dir = results_dir / "runs"

    def attempts_path(self, day: str) -> Path:
        return self.runs_dir / f"{day}.jsonl"

    def summary_path(self, day: str) -> Path:
        return self.runs_dir / f"{day}.run.json"

    def has_summary(self, day: str) -> bool:
        return self.summary_path(day).is_file()

    def has_attempts(self, day: str) -> bool:
        return self.attempts_path(day).is_file()

    def append_attempt(self, record: AttemptRecord) -> None:
        """Append one record as one line, flushed to disk before returning."""
        path = self.attempts_path(record.date)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json() + "\n"
        prefix = "\n" if self._ends_mid_line(path) else ""  # a killed writer left a fragment
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (prefix + line).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_attempts(self, day: str) -> tuple[list[AttemptRecord], int]:
        """The day's records and the number of unreadable lines that were skipped."""
        return self._read_file(self.attempts_path(day))

    def read_all_attempts(self) -> tuple[list[AttemptRecord], int]:
        records: list[AttemptRecord] = []
        corrupt = 0
        for path in sorted(self.runs_dir.glob("*.jsonl")):
            found, bad = self._read_file(path)
            records += found
            corrupt += bad
        return records, corrupt

    def recorded_attempt_ids(self, day: str) -> set[str]:
        return {r.attempt_id for r in self.read_attempts(day)[0]}

    def write_summary(self, summary: RunSummary) -> Path:
        """Create <date>.run.json. Fails (never overwrites) if it already exists."""
        path = self.summary_path(summary.date)
        self._create_exclusive(path, summary.model_dump_json(indent=2))
        return path

    def write_abort(self, abort: AbortRecord, at: datetime) -> Path:
        """Create <date>.abort-<HHMMSS>.json, moving to the next second on a name clash."""
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        moment = at
        while True:
            path = self.runs_dir / f"{abort.date}.abort-{moment:%H%M%S}.json"
            try:
                self._create_exclusive(path, abort.model_dump_json(indent=2))
            except FileExistsError:
                moment += timedelta(seconds=1)
            else:
                return path

    def read_aborts(self, day: str | None = None) -> list[AbortRecord]:
        pattern = f"{day}.abort-*.json" if day else "*.abort-*.json"
        return [self._load(AbortRecord, p) for p in sorted(self.runs_dir.glob(pattern))]

    def read_summaries(self) -> list[RunSummary]:
        return [self._load(RunSummary, p) for p in sorted(self.runs_dir.glob("*.run.json"))]

    @staticmethod
    def _load(model: Any, path: Path) -> Any:
        return model.model_validate_json(path.read_text())

    @staticmethod
    def _create_exclusive(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _ends_mid_line(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"

    @staticmethod
    def _read_file(path: Path) -> tuple[list[AttemptRecord], int]:
        if not path.is_file():
            return [], 0
        records: list[AttemptRecord] = []
        corrupt = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(AttemptRecord.model_validate_json(line))
            except ValidationError:
                corrupt += 1
        return records, corrupt


class TranscriptStore:
    """Redacted per-attempt messages as <dir>/<date>/<attempt_id>.json.gz (never committed)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, day: str, attempt_id: str, messages: list[dict[str, Any]]) -> bool:
        """Write one transcript. Failures are reported on stderr and never stop a run."""
        path = self._root / day / f"{attempt_id}.json.gz"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(messages, handle)
        except OSError as exc:
            print(f"warning: could not write transcript {path}: {exc}", file=sys.stderr)
            return False
        return True
