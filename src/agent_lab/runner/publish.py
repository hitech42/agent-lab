"""Publish a day's results from an untrusted directory into results/.

The directory comes from a job that ran agent code, so nothing in it is trusted: the
publisher accepts only a fixed set of relative paths, regular files of bounded size that
parse with their models, and only what keeps results/ append-only. Validation covers the
whole set before anything is written, so one violation means nothing is copied.

Like ``ResultsStore`` it tolerates a fragment left by a killed writer: unreadable lines in a
JSONL are counted (up to a small limit) rather than rejected.
"""

import os
import re
import stat
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from agent_lab.harness.config import AgentConfig
from agent_lab.harness.redact import Redactor
from agent_lab.runner.records import AbortRecord, AttemptRecord, ConfigRecord, RunSummary
from agent_lab.runner.schedule import ExperimentSchedule

FileKind = Literal["jsonl", "summary", "abort", "config"]

_DATE = r"\d{4}-\d{2}-\d{2}"
_RUN_FILES: tuple[tuple[FileKind, re.Pattern[str]], ...] = (
    ("jsonl", re.compile(rf"^(?P<date>{_DATE})\.jsonl$")),
    ("summary", re.compile(rf"^(?P<date>{_DATE})\.run\.json$")),
    ("abort", re.compile(rf"^(?P<date>{_DATE})\.abort-\d{{6}}\.json$")),
)
_CONFIG_FILE = re.compile(r"^(?P<hash>[0-9a-f]{12})\.json$")
_TRACK = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PublishRejected(Exception):
    """The incoming set breaks a rule. ``violations`` lists every problem found."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


@dataclass(frozen=True)
class FilePlan:
    """What to do with one validated incoming file."""

    rel: str
    action: Literal["copy", "append", "skip"]
    source: Path
    existing_size: int = 0


@dataclass(frozen=True)
class PublishPlan:
    files: list[FilePlan]
    unreadable_lines: int  # fragments counted (not rejected) across the incoming JSONL files


@dataclass(frozen=True)
class PublishReport:
    copied: list[str] = field(default_factory=list)
    appended: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    unreadable_lines: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.copied or self.appended)


@dataclass(frozen=True)
class _Found:
    rel: str
    path: Path
    kind: FileKind
    day: str | None
    config_hash: str | None = None


@dataclass(frozen=True)
class _Jsonl:
    records: list[AttemptRecord]
    unreadable: int
    blank_lines: list[int]


def _parse_jsonl(data: bytes) -> _Jsonl:
    """Records and the number of unreadable lines; blank lines (not the final newline) listed."""
    records: list[AttemptRecord] = []
    unreadable = 0
    blank: list[int] = []
    lines = data.split(b"\n")
    for number, raw in enumerate(lines, start=1):
        if number == len(lines) and not raw:
            continue  # the newline that ends the file
        if not raw.strip():
            blank.append(number)
            continue
        try:
            records.append(AttemptRecord.model_validate_json(raw.decode("utf-8")))
        except ValidationError, UnicodeDecodeError:
            unreadable += 1
    return _Jsonl(records, unreadable, blank)


def _read_regular(path: Path) -> bytes:
    """Read a file that was classified as regular, refusing to follow a link swapped in."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _valid_day(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


class ResultsPublisher:
    """Validates an incoming directory and copies it into results/ (append-only)."""

    def __init__(
        self,
        results_dir: Path,
        max_file_bytes: int = 5_000_000,
        max_files: int = 1000,
        max_unreadable_lines: int = 5,
    ) -> None:
        self._results_dir = results_dir
        self._max_file_bytes = max_file_bytes
        self._max_files = max_files
        self._max_unreadable_lines = max_unreadable_lines

    def plan(self, incoming: Path) -> PublishPlan:
        """Validate everything; raises PublishRejected listing every violation."""
        violations: list[str] = []
        found = self._scan(incoming, violations)
        files: list[FilePlan] = []
        unreadable = 0
        for item in found:
            data = self._read_checked(item, violations)
            if data is None:
                continue
            unreadable += self._check_content(item, data, violations)
            planned = self._plan_against_results(item, data, violations)
            if planned is not None:
                files.append(planned)
        if violations:
            raise PublishRejected(violations)
        return PublishPlan(files, unreadable)

    def apply(self, plan: PublishPlan) -> PublishReport:
        """Write a validated plan: new files are created, JSONL files only ever appended to."""
        copied: list[str] = []
        appended: list[str] = []
        skipped: list[str] = []
        for item in plan.files:
            target = self._results_dir / item.rel
            if item.action == "skip":
                skipped.append(item.rel)
                continue
            data = _read_regular(item.source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.action == "copy":
                with target.open("xb") as handle:  # never overwrites
                    handle.write(data)
                copied.append(item.rel)
            else:
                with target.open("ab") as handle:  # only the new suffix
                    handle.write(data[item.existing_size :])
                appended.append(item.rel)
        return PublishReport(copied, appended, skipped, plan.unreadable_lines)

    # --- scanning -------------------------------------------------------------------------

    def _scan(self, incoming: Path, violations: list[str]) -> list[_Found]:
        if not incoming.is_dir() or incoming.is_symlink():
            violations.append(f"{incoming} is not a directory")
            return []
        found: list[_Found] = []
        for entry in sorted(os.scandir(incoming), key=lambda e: e.name):
            if entry.name not in {"runs", "configs"}:
                violations.append(f"{entry.name}: not allowed at the top level")
            elif not self._is_real_dir(entry.path):
                violations.append(f"{entry.name}: must be a real directory")
            else:
                found += self._scan_dir(incoming, entry.name, violations)
        if len(found) > self._max_files:
            violations.append(f"too many files ({len(found)}; at most {self._max_files})")
        return found

    def _scan_dir(self, incoming: Path, name: str, violations: list[str]) -> list[_Found]:
        found: list[_Found] = []
        for entry in sorted(os.scandir(incoming / name), key=lambda e: e.name):
            rel = f"{name}/{entry.name}"
            info = os.lstat(entry.path)
            if stat.S_ISLNK(info.st_mode):
                violations.append(f"{rel}: symbolic links are not allowed")
            elif stat.S_ISDIR(info.st_mode):
                violations.append(f"{rel}: directories are not allowed here")
            elif not stat.S_ISREG(info.st_mode):
                violations.append(f"{rel}: not a regular file")
            elif info.st_size > self._max_file_bytes:
                violations.append(f"{rel}: {info.st_size} bytes exceeds {self._max_file_bytes}")
            else:
                item = self._classify(name, entry.name, Path(entry.path))
                if item is None:
                    violations.append(f"{rel}: name is not allowed")
                else:
                    found.append(item)
        return found

    @staticmethod
    def _classify(directory: str, name: str, path: Path) -> _Found | None:
        rel = f"{directory}/{name}"
        if directory == "configs":
            match = _CONFIG_FILE.match(name)
            return _Found(rel, path, "config", None, match["hash"]) if match else None
        for kind, pattern in _RUN_FILES:
            match = pattern.match(name)
            if match and _valid_day(match["date"]):
                return _Found(rel, path, kind, match["date"])
        return None

    @staticmethod
    def _is_real_dir(path: str) -> bool:
        return stat.S_ISDIR(os.lstat(path).st_mode)

    def _read_checked(self, item: _Found, violations: list[str]) -> bytes | None:
        try:
            return _read_regular(item.path)
        except OSError as exc:
            violations.append(f"{item.rel}: cannot be read ({exc.strerror or exc})")
            return None

    # --- content --------------------------------------------------------------------------

    def _check_content(self, item: _Found, data: bytes, violations: list[str]) -> int:
        """Parse one file with its model; returns the number of unreadable JSONL lines."""
        if item.kind == "jsonl":
            return self._check_jsonl(item, data, violations)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            violations.append(f"{item.rel}: is not UTF-8")
            return 0
        if item.kind == "config":
            self._check_config(item, text, violations)
        else:
            self._check_summary(item, text, violations)
        return 0

    def _check_jsonl(self, item: _Found, data: bytes, violations: list[str]) -> int:
        parsed = _parse_jsonl(data)
        for number in parsed.blank_lines:
            violations.append(f"{item.rel}: line {number} is blank")
        if parsed.unreadable > self._max_unreadable_lines:
            violations.append(
                f"{item.rel}: {parsed.unreadable} unreadable lines "
                f"(at most {self._max_unreadable_lines} are tolerated)"
            )
        seen: set[str] = set()
        for record in parsed.records:
            if record.date != item.day:
                violations.append(f"{item.rel}: {record.attempt_id} is dated {record.date}")
            if record.attempt_id in seen:
                violations.append(f"{item.rel}: duplicate attempt_id {record.attempt_id}")
            seen.add(record.attempt_id)
            self._check_track(item, record.track, violations)
        return parsed.unreadable

    def _check_summary(self, item: _Found, text: str, violations: list[str]) -> None:
        model: type[BaseModel] = RunSummary if item.kind == "summary" else AbortRecord
        try:
            parsed = model.model_validate_json(text)
        except ValidationError as exc:
            violations.append(
                f"{item.rel}: does not parse as {model.__name__} ({exc.error_count()} errors)"
            )
            return
        assert isinstance(parsed, RunSummary)
        if parsed.date != item.day:
            violations.append(f"{item.rel}: is dated {parsed.date}")
        self._check_track(item, parsed.track, violations)

    def _check_config(self, item: _Found, text: str, violations: list[str]) -> None:
        try:
            record = ConfigRecord.model_validate_json(text)
            config = AgentConfig(
                name=record.first_name,
                **record.model_dump(exclude={"schema_version", "config_hash", "first_name"}),
            )
        except ValidationError as exc:
            violations.append(
                f"{item.rel}: does not parse as ConfigRecord ({exc.error_count()} errors)"
            )
            return
        recomputed = ExperimentSchedule.config_hash(config)
        if not (item.config_hash == record.config_hash == recomputed):
            violations.append(
                f"{item.rel}: name, config_hash ({record.config_hash}) and the hash of its "
                f"content ({recomputed}) must all agree"
            )

    @staticmethod
    def _check_track(item: _Found, track: str, violations: list[str]) -> None:
        if not _TRACK.match(track):
            violations.append(f"{item.rel}: track {track!r} is not a slug")

    # --- against what is already published ------------------------------------------------

    def _plan_against_results(
        self, item: _Found, data: bytes, violations: list[str]
    ) -> FilePlan | None:
        target = self._results_dir / item.rel
        if not os.path.lexists(target):
            return FilePlan(item.rel, "copy", item.path)
        if target.is_symlink() or not target.is_file():
            violations.append(f"{item.rel}: the existing path in results/ is not a regular file")
            return None
        existing = target.read_bytes()
        if item.kind == "jsonl":
            if not data.startswith(existing):
                violations.append(
                    f"{item.rel}: does not start with the existing file "
                    "(it would rewrite history; results are append-only)"
                )
                return None
            grown = len(data) > len(existing)
            return FilePlan(item.rel, "append" if grown else "skip", item.path, len(existing))
        if data != existing:
            violations.append(f"{item.rel}: differs from the existing file, which must not change")
            return None
        return FilePlan(item.rel, "skip", item.path)


@dataclass(frozen=True)
class _Day:
    day: str
    track: str
    records: list[AttemptRecord]
    unreadable: int
    summary: RunSummary | None
    aborts: list[AbortRecord]


class CommitMessageBuilder:
    """One safe subject line describing the latest day in an incoming directory."""

    _MAX_CHARS = 200

    def __init__(self, redactor: Redactor | None = None) -> None:
        self._redactor = redactor or Redactor([])

    def build(self, incoming: Path) -> str:
        day = self._latest_day(incoming)
        if day is None:
            line = "eval: register configs" if (incoming / "configs").is_dir() else "eval: results"
            return self._finish(line)
        track = day.track if _TRACK.match(day.track) else "unknown"
        if day.summary is not None:
            head = self._terminal(track, day, day.summary)
        elif day.aborts:
            head = f"eval({track}): aborted ({self._cause(day.aborts[-1])}), day resumable"
        else:
            head = f"eval({track}): partial results, {len(day.records)} attempts, day resumable"
        if day.unreadable:
            head += f", {day.unreadable} unreadable line{'s' if day.unreadable != 1 else ''}"
        return self._finish(head)

    def _terminal(self, track: str, day: _Day, summary: RunSummary) -> str:
        counts = {
            role: (
                sum(1 for r in day.records if r.config_role == role and r.passed),
                sum(1 for r in day.records if r.config_role == role),
            )
            for role in ("baseline", "variant")
        }
        (bp, bn), (vp, vn) = counts["baseline"], counts["variant"]
        return (
            f"eval({track}): baseline {bp}/{bn} vs variant {vp}/{vn} passed, "
            f"${summary.total_cost_usd:.4f} [{summary.status}]"
        )

    @staticmethod
    def _cause(abort: AbortRecord) -> str:
        detail = abort.error_status if abort.error_status is not None else abort.error_type
        return f"{abort.reason}: {detail}" if detail is not None else abort.reason

    def _finish(self, line: str) -> str:
        """Redacted, a single line, bounded."""
        return " ".join(self._redactor.redact(line).split())[: self._MAX_CHARS]

    @staticmethod
    def _latest_day(incoming: Path) -> _Day | None:
        runs = incoming / "runs"
        names = sorted(p.name for p in runs.iterdir()) if runs.is_dir() else []
        days = sorted({n[:10] for n in names if re.match(rf"^{_DATE}\.", n)})
        if not days:
            return None
        latest = days[-1]
        records: list[AttemptRecord] = []
        unreadable = 0
        jsonl = runs / f"{latest}.jsonl"
        if jsonl.is_file():
            parsed = _parse_jsonl(jsonl.read_bytes())
            records, unreadable = parsed.records, parsed.unreadable
        summary: RunSummary | None = None
        summary_path = runs / f"{latest}.run.json"
        if summary_path.is_file():
            summary = RunSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
        aborts = [
            AbortRecord.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(runs.glob(f"{latest}.abort-*.json"))
        ]
        track = (
            summary.track
            if summary
            else aborts[-1].track
            if aborts
            else records[0].track
            if records
            else "unknown"
        )
        return _Day(latest, track, records, unreadable, summary, aborts)
