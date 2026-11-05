"""Sales tax."""

from decimal import Decimal

from money import to_cents


def tax_cents(subtotal_cents: int, rate: Decimal | str) -> int:
    """Tax on a subtotal, in whole cents.

    ``rate`` is a fraction: ``"0.0825"`` is 8.25%. The tax is computed once, on the
    whole subtotal, and rounded like every other amount (see ``money.to_cents``);
    it is never computed line by line.
    """
    return to_cents(Decimal(subtotal_cents) / 100 * Decimal(str(rate)))
