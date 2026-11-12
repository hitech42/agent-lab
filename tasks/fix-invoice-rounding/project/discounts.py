"""Discounts on an invoice.

A ``Discount`` is either a percentage (``kind="percent"``, ``value`` a ``Decimal`` percentage:
``Decimal("12.5")`` is 12.5% off) or a fixed amount (``kind="fixed"``, ``value`` in cents).

An invoice carries a list of discounts, in any order. They apply in this order whatever the
order of the list: every percentage first, each on what the earlier ones left, then every
fixed amount. A discount never takes off more than what is left of the amount it applies
to, so the total discount never exceeds the base. The base is the sum of the positive line
amounts; a credit line is not discounted. A percentage is rounded once, as ``money`` describes.

The invoice's total discount is spread over its lines with ``money.allocate``, using the
line amounts as weights; a credit line weighs 0 and gets no discount.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from money import allocate, format_cents, to_cents


@dataclass(frozen=True)
class Discount:
    kind: Literal["percent", "fixed"]
    value: Decimal | int

    def __post_init__(self) -> None:
        if self.kind == "percent" and not 0 <= Decimal(self.value) <= 100:
            raise ValueError("a percentage must be between 0 and 100")
        if self.kind == "fixed" and int(self.value) < 0:
            raise ValueError("a fixed discount cannot be negative")

    def describe(self) -> str:
        """``12.5% off`` or ``$5.00 off``."""
        if self.kind == "percent":
            return f"{self.value}% off"
        return f"{format_cents(int(self.value))} off"


def take_off(left_cents: int, discount: Discount) -> int:
    """The cents this discount takes off an amount of ``left_cents``, never more than that."""
    if discount.kind == "percent":
        wanted = to_cents(Decimal(left_cents) / 100 * Decimal(discount.value) / 100)
    else:
        wanted = int(discount.value)
    return max(0, min(wanted, left_cents))


def ordered(discounts: list[Discount]) -> list[Discount]:
    """The discounts in the order they apply: percentages, then fixed amounts."""
    return [d for d in discounts if d.kind == "percent"] + [
        d for d in discounts if d.kind == "fixed"
    ]


def total_discount(base_cents: int, discounts: list[Discount]) -> int:
    """The total discount of ``discounts`` on a base, applied as the module docstring says."""
    left = base_cents
    for discount in ordered(discounts):
        left -= take_off(left, discount)
    return base_cents - left


def spread(discount_cents: int, line_cents: list[int]) -> list[int]:
    """The discount each line gets, following the module docstring."""
    weights = [max(0, cents) for cents in line_cents]
    if discount_cents == 0 or sum(weights) == 0:
        return [0] * len(line_cents)
    return allocate(discount_cents, weights)
