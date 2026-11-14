"""The data a failure triage produces (kept apart so records can import it without cycles)."""

from dataclasses import dataclass
from typing import Literal

FailureLabel = Literal[
    "out_of_turns",
    "budget_exhausted",
    "truncated_or_refused",
    "protected_violation",
    "injection_followed",
    "environment_tampered",
    "infrastructure_error",
    "regression",
    "no_verification",
    "incomplete_fix",
    "wrong_approach",
    "misread_spec",
    "other",
]
TriageSource = Literal["rule", "model", "error"]
NOTE_MAX_CHARS = 200


@dataclass(frozen=True)
class TriageOutcome:
    """A label with where it came from, a one-line note, and what the model call cost."""

    label: FailureLabel
    source: TriageSource
    note: str
    cost_usd: float = 0.0
    tokens: int = 0
