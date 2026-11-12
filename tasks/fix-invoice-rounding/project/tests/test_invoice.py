from decimal import Decimal

from discounts import Discount
from invoice import build_invoice, render
from money import allocate, format_cents, round_div, to_cents
from pricing import price_line


def test_format_cents():
    assert format_cents(123456) == "$1,234.56"
    assert format_cents(-5) == "-$0.05"


def test_to_cents_for_plain_amounts():
    assert to_cents("19.99") == 1999
    assert to_cents(Decimal("3")) == 300


def test_round_div_and_an_even_allocation():
    assert round_div(9, 3) == 3
    assert allocate(90, [1, 1, 1]) == [30, 30, 30]


def test_a_line_is_priced_from_the_catalog():
    assert price_line("CABLE", 2).gross_cents == 499
    assert price_line("MANUAL", 1).gross_cents == 1999


def test_a_simple_invoice():
    invoice = build_invoice([("CABLE", 2)])
    assert (invoice.subtotal_cents, invoice.tax_cents, invoice.total_cents) == (499, 41, 540)


def test_a_percentage_discount():
    invoice = build_invoice([("MANUAL", 1)], [Discount("percent", Decimal("10"))])
    assert (invoice.discount_cents, invoice.tax_cents, invoice.total_cents) == (200, 72, 1871)


def test_render_prints_each_part():
    text = render(build_invoice([("CABLE", 2)]))
    assert text.splitlines() == [
        "Cable: 2 = $4.99",
        "Subtotal: $4.99",
        "Tax: $0.41",
        "Total: $5.40",
    ]
