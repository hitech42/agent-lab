from decimal import Decimal

import pytest
from discount import discount_cents
from invoice import build_invoice, price_line, render
from money import to_cents
from tax import tax_cents


@pytest.mark.parametrize(
    ("amount", "cents"),
    [
        ("0.125", 13),
        ("0.145", 15),
        ("2.675", 268),
        ("1.005", 101),
        ("0.005", 1),
        ("0.004", 0),
        ("-0.125", -13),
        ("-2.675", -268),
        ("19.99", 1999),
        ("10", 1000),
        ("0", 0),
    ],
)
def test_amounts_round_to_the_nearest_cent_with_halves_away_from_zero(amount, cents):
    assert to_cents(amount) == cents
    assert to_cents(Decimal(amount)) == cents


def test_to_cents_is_exact_for_large_amounts():
    assert to_cents("12345678901234.565") == 1234567890123457


def test_tax_is_computed_on_the_subtotal_and_rounded_like_other_amounts():
    assert tax_cents(1000, "0.0825") == 83
    assert tax_cents(1999, "0.07") == 140
    assert tax_cents(0, "0.07") == 0


def test_discounts_are_rounded_like_other_amounts():
    assert discount_cents(1000, "12.5") == 125
    assert discount_cents(1001, "12.5") == 125
    assert discount_cents(1004, "12.5") == 126


def test_a_line_total_is_rounded_once_after_multiplying():
    assert price_line("x", "0.335", 3).total_cents == 101


def test_a_negative_unit_price_is_a_credit():
    assert price_line("credit", "-0.125", 1).total_cents == -13


def test_invoice_totals():
    invoice = build_invoice([("A", "0.125", 1), ("B", "2.675", 1)], tax_rate="0.10")
    assert [line.total_cents for line in invoice.lines] == [13, 268]
    assert invoice.subtotal_cents == 281
    assert invoice.tax_cents == 28
    assert invoice.total_cents == 309


def test_the_discount_comes_off_before_tax_is_computed():
    invoice = build_invoice([("A", "100.00", 1)], tax_rate="0.0825", discount_percent="12.5")
    assert invoice.subtotal_cents == 10000
    assert invoice.discount_cents == 1250
    assert invoice.tax_cents == 722  # 8.25% of 87.50 is 7.21875
    assert invoice.total_cents == 9472


def test_render_with_a_discount():
    invoice = build_invoice(
        [("Widget", "0.125", 1), ("Gadget", "2.675", 1)], tax_rate="0.10", discount_percent="10"
    )
    assert render(invoice) == (
        "Widget: 1 x 0.125 = $0.13\n"
        "Gadget: 1 x 2.675 = $2.68\n"
        "Subtotal: $2.81\n"
        "Discount: -$0.28\n"
        "Tax: $0.25\n"
        "Total: $2.78"
    )


def test_render_without_a_discount_has_no_discount_line():
    text = render(build_invoice([("Widget", "0.125", 1)], tax_rate="0.10"))
    assert text == "Widget: 1 x 0.125 = $0.13\nSubtotal: $0.13\nTax: $0.01\nTotal: $0.14"
