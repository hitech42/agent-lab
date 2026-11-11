"""What a run tells the workflow: kind, status, a safe one-line detail and the attempt counts.

`agent-lab run` exits 0 whenever a record was written, even for an abort, so the daily workflow
cannot tell a good day from a bad one by the exit code. `--outcome-file` writes a `RunReport`
instead. Its values end up in job outputs and in workflow commands (`::error::`), so they are
reduced to a small safe character set: no newline, no `::`, no `%`, no secret.
"""

import re
from dataclasses import dataclass
from typing import Literal

from agent_lab.harness.redact import Redactor
from agent_lab.runner.experiment import RunOutcome

Kind = Literal["abort", "summary", "already_done", "dry_run"]

# What the workflow accepts from the outcome file (keep in step with daily.yml).
SAFE_VALUE = r"[A-Za-z0-9 .,:_'()/-]*"
_UNSAFE = re.compile(r"[^A-Za-z0-9 .,:_'()/-]+|:{2,}")  # also any "::" (a workflow command)


@dataclass(frozen=True)
class RunReport:
    """The outcome of one `agent-lab run`, as plain data."""

    kind: Kind
    status: str  # summary: the RunSummary status; abort: its reason; else the kind itself
    detail: str = ""  # "<http status> <error type>: <message>" for an abort, redacted and safe
    completed: int | None = None  # attempt counts, for runs that wrote a summary or an abort
    planned: int | None = None

    @classmethod
    def already_done(cls) -> RunReport:
        return cls("already_done", "already_done")

    @classmethod
    def dry_run(cls) -> RunReport:
        return cls("dry_run", "dry_run")

    @classmethod
    def of(cls, outcome: RunOutcome, redactor: Redactor, max_detail: int = 160) -> RunReport:
        """The report for a finished or aborted run; free text goes through the redactor."""
        summary = outcome.summary
        if outcome.kind == "abort":
            cause = outcome.cause
            reason = cause.reason if cause else "unknown"
            parts = [str(cause.error_status)] if cause and cause.error_status is not None else []
            if cause and cause.error_type:
                parts.append(cause.error_type)
            head = " ".join(parts)
            message = cause.message if cause and cause.message else ""
            raw = f"{head}: {message}" if head and message else head or message
            return cls(
                "abort",
                cls._safe(reason, redactor, 80),
                cls._safe(raw, redactor, max_detail),
                summary.completed,
                summary.planned,
            )
        return cls(
            "summary",
            cls._safe(summary.status, redactor, 80),
            "",
            summary.completed,
            summary.planned,
        )

    @property
    def alert(self) -> bool:
        """An abort or a tampered environment: the workflow publishes the record, then fails."""
        return self.kind == "abort" or self.status == "aborted_tampered"

    def lines(self) -> list[str]:
        """`key=value` lines for the workflow (one per line, values already safe)."""
        lines = [f"kind={self.kind}", f"status={self.status}", f"detail={self.detail}"]
        if self.completed is not None and self.planned is not None:
            lines += [f"completed={self.completed}", f"planned={self.planned}"]
        return lines

    @staticmethod
    def _safe(text: str, redactor: Redactor, limit: int) -> str:
        cleaned = _UNSAFE.sub(" ", redactor.redact(text))  # never "::", newlines or "%"
        return " ".join(cleaned.split())[:limit].strip()
