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


def tier_units(product: Product, quantity: int) -> list[int]:
    """How many of ``quantity`` units fall in each of the product's tiers, in tier order.

    The units are numbered from 1, as the ``catalog`` docstring describes. The counts add up
    to ``quantity``.
    """
    counts = []
    for index, tier in enumerate(product.tiers):
        end = product.tiers[index + 1].start - 1 if index + 1 < len(product.tiers) else quantity
        counts.append(max(0, min(quantity, end) - tier.start + 1))
    return counts


def price_line(sku: str, quantity: int) -> Line:
    """Price ``quantity`` units of the product with this SKU, as the ``catalog`` describes.

    ``quantity`` must be positive; otherwise ``ValueError``.
    """
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    product = find(sku)
    counts = tier_units(product, quantity)
    total = sum(units * tier.tenths for units, tier in zip(counts, product.tiers, strict=True))
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
