"""Money helpers, and the rounding conventions that every module of the package follows.

* An amount is a whole number of cents (``int``). A credit is a negative amount.
* An amount is rounded once, at the end of the computation that produces it. A rounded
  figure is never used as an intermediate result of another computation.
* Rounding is to the nearest cent, and an amount exactly halfway between two cents rounds
  away from zero: 0.5 cents is 1 cent, -0.5 is -1, 2.5 is 3 and -2.5 is -3.
* The shares into which ``allocate`` splits a total always add up to the total exactly: each
  share starts as its exact part rounded down, and the cents left over go one each to the
  shares with the largest fractional remainders, a tie going to the earlier share.
* Amounts print as ``$1,234.56``, and a negative amount as ``-$1,234.56``.
"""

from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: Decimal | str) -> int:
    """Convert an amount in currency units (a ``Decimal`` or a decimal string) to cents.

    Rounded as described in the module docstring: ``"0.125"`` is 13 cents and ``"-0.125"``
    is -13 cents. The result is exact for any decimal amount.
    """
    exact = Decimal(amount) * 100
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def round_div(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` as a whole number, rounded as the module docstring says.

    ``denominator`` must be positive. ``round_div(5, 2)`` is 3 and ``round_div(-5, 2)`` is -3.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    whole, remainder = divmod(abs(numerator), denominator)
    if 2 * remainder >= denominator:
        whole += 1
    return -whole if numerator < 0 else whole


def allocate(total_cents: int, weights: list[int]) -> list[int]:
    """Split ``total_cents`` into one share per weight, in proportion to the weights.

    The shares follow the module docstring: they add up to ``total_cents`` exactly. A weight
    of 0 gets a share of 0 unless the leftover cents reach it, which they never do while any
    weight is positive. ``total_cents`` must not be negative, the weights must not be
    negative and must not all be zero; otherwise ``ValueError``.
    """
    whole = sum(weights)
    if total_cents < 0 or min(weights, default=0) < 0 or whole == 0:
        raise ValueError("nothing to allocate over")
    shares = [total_cents * weight // whole for weight in weights]
    shares[-1] += total_cents - sum(shares)
    return shares


def format_cents(cents: int) -> str:
    """Format whole cents as ``$1,234.56``; a negative amount as ``-$1,234.56``."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{fraction:02d}"
