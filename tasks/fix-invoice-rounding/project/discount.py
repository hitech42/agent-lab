"""Discounts."""

from decimal import Decimal

from money import to_cents


def discount_cents(subtotal_cents: int, percent: Decimal | str) -> int:
    """The discount for ``percent`` off a subtotal, in whole cents.

    ``percent`` is a percentage: ``"12.5"`` is 12.5% off. The discount is rounded like
    every other amount (see ``money.to_cents``).
    """
    return to_cents(Decimal(subtotal_cents) / 100 * Decimal(str(percent)) / 100)
