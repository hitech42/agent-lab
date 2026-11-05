"""Money helpers. Amounts are passed around as whole cents (``int``)."""

from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: Decimal | str) -> int:
    """Convert an amount in currency units to a whole number of cents.

    The amount is rounded to the nearest cent. An amount exactly halfway between two
    cents rounds away from zero: ``"0.125"`` is 13 cents and ``"-0.125"`` is -13 cents.
    The result is exact for any amount given as a ``Decimal`` or a decimal string.
    """
    cents = (Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(cents)


def format_cents(cents: int) -> str:
    """Format whole cents as ``$1,234.56``; a negative amount as ``-$1,234.56``."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{fraction:02d}"
