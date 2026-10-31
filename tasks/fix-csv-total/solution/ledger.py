"""Sum the money columns of CSV ledger exports."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path


def data_lines(lines: Iterable[str]) -> Iterator[str]:
    """Yield the lines that are neither blank nor ``#`` comments."""
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#"):
            yield line


def read_rows(path: str | Path) -> list[dict[str, str]]:
    """Read a ledger as one dict per data row, keyed by the column names in its header.

    The first line that is not blank and not a ``#`` comment is the header.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(data_lines(handle)))


def parse_amount(text: str) -> Decimal:
    """Parse one amount, allowing thousands separators; ``ValueError`` if invalid."""
    try:
        value = Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"invalid amount: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"invalid amount: {text!r}")
    return value


def total_column(path: str | Path, column: str = "amount") -> Decimal:
    """Exact sum of ``column`` over all data rows, as a ``Decimal``.

    The column is chosen by name from the header, so column order does not matter.
    Amounts may be negative and may contain thousands separators (``"1,200.50"``).
    Raises ``ValueError`` if an amount cannot be parsed. A ledger with a header and
    no data rows totals ``Decimal("0")``.
    """
    total = Decimal("0")
    for row in read_rows(path):
        total += parse_amount(row[column])
    return total


def row_count(path: str | Path) -> int:
    """Number of transactions (data rows) in the ledger."""
    return len(read_rows(path))


def column_names(path: str | Path) -> list[str]:
    """The column names from the ledger's header row."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(data_lines(handle))
        return next(reader, [])


def format_money(amount: Decimal) -> str:
    """Format an amount with two decimals and thousands separators."""
    return f"{amount:,.2f}"


def largest_amount(path: str | Path, column: str = "amount") -> Decimal:
    """The largest single amount in ``column`` (``Decimal("0")`` for an empty ledger)."""
    values = [parse_amount(row[column]) for row in read_rows(path)]
    return max(values, default=Decimal("0"))


def is_refund(amount: Decimal) -> bool:
    """True for negative amounts, which are refunds."""
    return amount < 0


def split_refunds(amounts: Iterable[Decimal]) -> tuple[Decimal, Decimal]:
    """Return ``(charges, refunds)`` totals; refunds are reported as a positive number."""
    charges = Decimal("0")
    refunds = Decimal("0")
    for amount in amounts:
        if is_refund(amount):
            refunds -= amount
        else:
            charges += amount
    return charges, refunds
