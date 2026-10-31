"""Parse date ranges such as ``2026-01-28..2026-02-03`` and count the days they cover."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

SEPARATOR = ".."


def parse_range(text: str) -> tuple[date, date]:
    """Parse ``START..END`` (ISO dates) into a ``(start, end)`` pair.

    Both ends are part of the range. Leading and trailing whitespace around the
    whole text is ignored. Raises ``ValueError`` if ``text`` is not exactly two
    ISO dates joined by ``..`` (an empty, missing or invalid date makes it
    malformed), or if the range is reversed (END before START).
    """
    parts = text.strip().split(SEPARATOR)
    start = date.fromisoformat(parts[0])
    end = date.fromisoformat(parts[1])
    return start, end


def inclusive_days(start: date, end: date) -> int:
    """Number of days from ``start`` to ``end``, counting both ends.

    A range whose start and end are the same day covers exactly one day.
    """
    if start == end:
        return 0
    return (end - start).days


def days_in_range(text: str) -> int:
    """Number of days covered by a ``START..END`` range, both ends included.

    Raises ``ValueError`` exactly when ``parse_range`` does (malformed or reversed).
    """
    start, end = parse_range(text)
    return inclusive_days(start, end)


def overlaps(first: str, second: str) -> bool:
    """True if two ranges share at least one day."""
    a_start, a_end = parse_range(first)
    b_start, b_end = parse_range(second)
    return a_start <= b_end and b_start <= a_end


def iter_days(text: str) -> Iterator[date]:
    """Yield every day of a ``START..END`` range in order, both ends included."""
    start, end = parse_range(text)
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def weekdays_in_range(text: str) -> int:
    """Number of Monday-to-Friday days in a ``START..END`` range."""
    return sum(1 for day in iter_days(text) if day.weekday() < 5)


def format_range(start: date, end: date) -> str:
    """Format a ``(start, end)`` pair the way ``parse_range`` reads it."""
    return f"{start.isoformat()}{SEPARATOR}{end.isoformat()}"


def split_range(text: str, chunk_days: int) -> list[str]:
    """Cut a range into consecutive ranges of at most ``chunk_days`` days each."""
    if chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")
    start, end = parse_range(text)
    chunks = []
    while start <= end:
        stop = min(start + timedelta(days=chunk_days - 1), end)
        chunks.append(format_range(start, stop))
        start = stop + timedelta(days=1)
    return chunks
