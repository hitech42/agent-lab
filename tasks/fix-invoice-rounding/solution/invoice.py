"""Build and print invoices from the price list, the discounts and the tax rules.

``pricing`` prices each line, ``discounts`` says how the invoice's discounts apply and are
spread over the lines, ``tax`` says how tax is charged, and ``money`` has the rounding
conventions. This module only puts their results together.
"""

from dataclasses import dataclass

from discounts import Discount, spread, total_discount
from money import format_cents
from pricing import Line, price_line
from tax import invoice_tax


@dataclass(frozen=True)
class InvoiceLine:
    line: Line
    discount_cents: int

    @property
    def net_cents(self) -> int:
        return self.line.gross_cents - self.discount_cents

    def describe(self) -> str:
        """``<name>: <quantity> = <line charge>``, as ``render`` prints a line."""
        return f"{self.line.name}: {self.line.quantity} = {format_cents(self.line.gross_cents)}"


@dataclass(frozen=True)
class Invoice:
    lines: list[InvoiceLine]
    discount_cents: int
    tax_cents: int

    @property
    def subtotal_cents(self) -> int:
        """The sum of the line charges before any discount."""
        return sum(item.line.gross_cents for item in self.lines)

    @property
    def total_cents(self) -> int:
        """Subtotal minus discount plus tax."""
        return self.subtotal_cents - self.discount_cents + self.tax_cents

    @property
    def credit_cents(self) -> int:
        """The sum of the credit lines, as a positive amount (0 if there are none)."""
        return -sum(item.line.gross_cents for item in self.lines if item.line.gross_cents < 0)

    def summary(self) -> dict[str, int]:
        """The figures ``render`` prints at the end, by name, in cents."""
        return {
            "subtotal": self.subtotal_cents,
            "discount": self.discount_cents,
            "tax": self.tax_cents,
            "total": self.total_cents,
        }

    def count_units(self) -> int:
        """The number of units on the invoice, all lines together."""
        return sum(item.line.quantity for item in self.lines)


def build_invoice(items: list[tuple[str, int]], discounts: list[Discount] | None = None) -> Invoice:
    """Build an invoice from ``(sku, quantity)`` items and the invoice's discounts.

    Lines are priced by ``pricing``. The total discount is worked out by ``discounts`` on the
    positive line amounts and spread over the lines; tax is charged by ``tax`` on what the
    lines come to after their share of the discount.
    """
    lines = [price_line(sku, quantity) for sku, quantity in items]
    gross = [line.gross_cents for line in lines]
    base = sum(cents for cents in gross if cents > 0)
    discount = total_discount(base, discounts or [])
    shares = spread(discount, gross)
    items_out = [InvoiceLine(line, share) for line, share in zip(lines, shares, strict=True)]
    tax = invoice_tax((item.line.group, item.net_cents) for item in items_out)
    return Invoice(items_out, discount, tax)


def render(invoice: Invoice) -> str:
    """The invoice as text, lines joined by newlines, with no trailing newline.

    One line per item, ``<name>: <quantity> = <line charge>``, then ``Subtotal: <amount>``,
    ``Discount: -<amount>`` only when the discount is not zero, ``Tax: <amount>`` and
    ``Total: <amount>``. Amounts are formatted with ``money.format_cents``. The printed
    figures always add up.
    """
    out = [item.describe() for item in invoice.lines]
    out.append(f"Subtotal: {format_cents(invoice.subtotal_cents)}")
    if invoice.discount_cents:
        out.append(f"Discount: -{format_cents(invoice.discount_cents)}")
    out.append(f"Tax: {format_cents(invoice.tax_cents)}")
    out.append(f"Total: {format_cents(invoice.total_cents)}")
    return "\n".join(out)
