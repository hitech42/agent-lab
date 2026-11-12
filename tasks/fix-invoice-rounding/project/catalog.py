"""The price list.

A ``Product`` is sold per ``per`` units (a price per 100 bolts is ``per=100``). Its price
is given in tenths of a cent, so that a fraction of a cent per unit can be written exactly.
A negative price is a credit.

Prices are graduated. A product has one or more ``Tier``s, the first starting at unit 1. A
tier's ``start`` is the number of the first unit it applies to, and each unit is charged the
price of the tier that unit falls in: with tiers starting at 1 and 1000, units 1 to 999 are
charged the first price and unit 1000 and every unit after it the second. A larger order
never makes the units it already had cheaper than the tier they fall in.

The charge for a line is the sum of the units in each tier times that tier's price, divided
by ``10 * per`` to give cents (see ``amount_cents``).

Every product belongs to a tax ``group``, which ``tax`` knows the rate of.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    start: int  # number of the first unit this tier applies to
    tenths: int  # tenths of a cent per ``per`` units, for each unit in the tier


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    group: str
    per: int
    tiers: tuple[Tier, ...]


_PRODUCTS = (
    Product("BOLT", "Bolts", "standard", 100, (Tier(1, 455), Tier(1000, 400))),
    Product("CABLE", "Cable", "standard", 1, (Tier(1, 2495), Tier(50, 2250))),
    Product("MANUAL", "Manual", "reduced", 1, (Tier(1, 19990),)),
    Product("SERVICE", "Service call", "exempt", 1, (Tier(1, 75000),)),
    Product("LOYALTY", "Loyalty credit", "standard", 1, (Tier(1, -125),)),
)


def find(sku: str) -> Product:
    """The product with this SKU; ``KeyError`` if there is none."""
    for product in _PRODUCTS:
        if product.sku == sku:
            return product
    raise KeyError(sku)


def amount_cents(total_tenths: int, per: int) -> int:
    """The charge in whole cents for ``total_tenths`` tenths of a cent at prices per ``per``.

    That is ``total_tenths / (10 * per)``, rounded as described in ``money``.
    """
    return (2 * total_tenths + 10 * per) // (20 * per)


def describe(product: Product) -> str:
    """One line for a price list: ``<sku> <name> (<group>), per <per>: <tier prices>``.

    Each tier is written ``from <start>: <tenths>``, the prices in tenths of a cent.
    """
    tiers = ", ".join(f"from {tier.start}: {tier.tenths}" for tier in product.tiers)
    return f"{product.sku} {product.name} ({product.group}), per {product.per}: {tiers}"


def price_list() -> list[str]:
    """``describe`` for every product, in catalog order."""
    return [describe(product) for product in _PRODUCTS]


def skus() -> list[str]:
    """Every SKU of the catalog, in catalog order."""
    return [product.sku for product in _PRODUCTS]
