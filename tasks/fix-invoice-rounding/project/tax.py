"""Sales tax.

Every product has a tax group and every group has a rate. Tax is charged per group: the
amounts of all the lines of a group (each after its share of the discount) are added up and
the tax is computed once on that sum and rounded once, as ``money`` describes. It is never
computed line by line. The invoice's tax is the sum of its groups' taxes. A group whose sum
is negative (a credit) has negative tax.
"""

from collections.abc import Iterable
from decimal import Decimal

from money import to_cents

_RATES = {"standard": Decimal("0.0825"), "reduced": Decimal("0.04"), "exempt": Decimal("0")}


def rate(group: str) -> Decimal:
    """The tax rate of a group, as a fraction: ``0.0825`` is 8.25%. ``KeyError`` if unknown."""
    return _RATES[group]


def group_tax(group: str, base_cents: int) -> int:
    """The tax in cents on ``base_cents``, the sum of the net amounts of one group."""
    return to_cents(Decimal(base_cents) / 100 * rate(group))


def group_bases(net_lines: Iterable[tuple[str, int]]) -> dict[str, int]:
    """The sum of the net amounts of each group, from ``(group, net cents)`` for each line."""
    bases: dict[str, int] = {}
    for group, cents in net_lines:
        bases[group] = bases.get(group, 0) + cents
    return bases


def breakdown(net_lines: Iterable[tuple[str, int]]) -> dict[str, int]:
    """The tax of each group that appears, in order of first appearance."""
    return {group: group_tax(group, base) for group, base in group_bases(net_lines).items()}


def invoice_tax(net_lines: Iterable[tuple[str, int]]) -> int:
    """The tax of an invoice, from ``(group, net cents)`` for each line."""
    return sum(breakdown(net_lines).values())


def groups() -> list[str]:
    """The names of all tax groups."""
    return list(_RATES)


def describe_rates() -> list[str]:
    """One ``<group>: <rate>%`` line per group, for a footer."""
    return [f"{group}: {rate(group) * 100:.2f}%" for group in groups()]


def is_taxed(group: str) -> bool:
    """Whether a group has a rate above zero."""
    return rate(group) > 0
