"""Build and print invoices."""

from dataclasses import dataclass
from decimal import Decimal

from discount import discount_cents
from money import format_cents, to_cents
from tax import tax_cents


@dataclass(frozen=True)
class Line:
    description: str
    unit_price: str
    quantity: int
    total_cents: int


@dataclass(frozen=True)
class Invoice:
    lines: list[Line]
    subtotal_cents: int
    discount_cents: int
    tax_cents: int

    @property
    def total_cents(self) -> int:
        """Subtotal minus discount plus tax."""
        return self.subtotal_cents - self.discount_cents + self.tax_cents


def price_line(description: str, unit_price: str, quantity: int) -> Line:
    """Price one line: unit price times quantity, in whole cents.

    The product is rounded once, after multiplying, never before: three items at
    ``"0.335"`` are 101 cents, not three times the rounded unit price. A negative unit
    price is a credit and gives a negative line total.
    """
    total = to_cents(Decimal(unit_price) * quantity)
    return Line(description, unit_price, quantity, total)


def build_invoice(
    items: list[tuple[str, str, int]],
    tax_rate: Decimal | str = "0",
    discount_percent: Decimal | str = "0",
) -> Invoice:
    """Build an invoice from ``(description, unit price, quantity)`` items.

    The subtotal is the sum of the line totals. The discount is a percentage of the
    subtotal (see ``discount.discount_cents``) and tax is computed on the subtotal after
    the discount (see ``tax.tax_cents``).
    """
    lines = [price_line(*item) for item in items]
    subtotal = sum(line.total_cents for line in lines)
    discount = discount_cents(subtotal, discount_percent)
    return Invoice(lines, subtotal, discount, tax_cents(subtotal - discount, tax_rate))


def render(invoice: Invoice) -> str:
    """The invoice as text, lines joined by newlines, with no trailing newline.

    One line per item, ``<description>: <quantity> x <unit price> = <line total>``, then
    ``Subtotal: <amount>``, ``Discount: -<amount>`` only when the discount is not zero,
    ``Tax: <amount>`` and ``Total: <amount>``. Amounts are formatted with
    ``money.format_cents``. The printed figures always add up.
    """
    out = [
        f"{line.description}: {line.quantity} x {line.unit_price} = "
        f"{format_cents(line.total_cents)}"
        for line in invoice.lines
    ]
    out.append(f"Subtotal: {format_cents(invoice.subtotal_cents)}")
    if invoice.discount_cents:
        out.append(f"Discount: -{format_cents(invoice.discount_cents)}")
    out.append(f"Tax: {format_cents(invoice.tax_cents)}")
    out.append(f"Total: {format_cents(invoice.total_cents)}")
    return "\n".join(out)
