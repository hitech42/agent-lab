from decimal import Decimal

from invoice import build_invoice, render
from money import format_cents, to_cents


def test_format_cents():
    assert format_cents(123456) == "$1,234.56"
    assert format_cents(-5) == "-$0.05"


def test_to_cents_for_plain_amounts():
    assert to_cents("19.99") == 1999
    assert to_cents(Decimal("3")) == 300


def test_a_simple_invoice():
    invoice = build_invoice([("Pen", "1.50", 2), ("Pad", "4.00", 1)], tax_rate="0.10")
    assert [line.total_cents for line in invoice.lines] == [300, 400]
    assert (invoice.subtotal_cents, invoice.tax_cents, invoice.total_cents) == (700, 70, 770)


def test_render_prints_each_part():
    text = render(build_invoice([("Pen", "1.50", 2)], tax_rate="0.10"))
    assert text.splitlines() == [
        "Pen: 2 x 1.50 = $3.00",
        "Subtotal: $3.00",
        "Tax: $0.30",
        "Total: $3.30",
    ]


def test_tax_on_a_round_amount_is_rounded_to_the_nearest_cent():
    invoice = build_invoice([("Item", "10.00", 1)], tax_rate="0.0825")
    assert invoice.tax_cents == 83
