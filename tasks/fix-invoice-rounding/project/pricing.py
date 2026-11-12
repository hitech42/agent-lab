"""Price one invoice line from the catalog."""

from dataclasses import dataclass

from catalog import Product, amount_cents, find


@dataclass(frozen=True)
class Line:
    sku: str
    name: str
    group: str
    quantity: int
    gross_cents: int  # the charge before any discount


def price_line(sku: str, quantity: int) -> Line:
    """Price ``quantity`` units of the product with this SKU, as the ``catalog`` describes.

    ``quantity`` must be positive; otherwise ``ValueError``.
    """
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    product = find(sku)
    tier = [tier for tier in product.tiers if tier.start <= quantity][-1]
    total = quantity * tier.tenths
    return Line(sku, product.name, product.group, quantity, amount_cents(total, product.per))


def price_all(items: list[tuple[str, int]]) -> list[Line]:
    """``price_line`` for every ``(sku, quantity)`` item, in order."""
    return [price_line(sku, quantity) for sku, quantity in items]


def merge_lines(lines: list[Line]) -> list[Line]:
    """Lines of the same SKU combined into one, at the position of the first of them.

    The combined line is priced for the total quantity, so that tiers apply to it as a whole.
    """
    quantities: dict[str, int] = {}
    for line in lines:
        quantities[line.sku] = quantities.get(line.sku, 0) + line.quantity
    return [price_line(sku, quantity) for sku, quantity in quantities.items()]
