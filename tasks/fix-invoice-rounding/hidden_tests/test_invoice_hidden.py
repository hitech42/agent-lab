from decimal import Decimal

import pytest
from catalog import amount_cents
from discounts import Discount
from invoice import build_invoice, render
from money import allocate, round_div
from pricing import price_line


def percent(value):
    return Discount("percent", Decimal(value))


def fixed(cents):
    return Discount("fixed", cents)


# money.allocate: largest remainders get the leftover cents, ties to the earlier share


@pytest.mark.parametrize(
    ("total", "weights", "shares"),
    [
        (100, [1, 1, 1], [34, 33, 33]),
        (10, [2, 1], [7, 3]),
        (1, [1, 1, 1], [1, 0, 0]),
        (5, [0, 1, 1], [0, 3, 2]),
        (7, [3, 3, 1], [3, 3, 1]),
        (0, [4, 5], [0, 0]),
    ],
)
def test_allocate_gives_the_leftover_cents_to_the_largest_remainders(total, weights, shares):
    assert allocate(total, weights) == shares
    assert sum(shares) == total


def test_allocate_always_adds_up():
    for total in range(0, 40):
        for weights in ([1, 2, 3], [5, 1, 1, 1], [7, 0, 2]):
            shares = allocate(total, weights)
            assert sum(shares) == total
            assert len(shares) == len(weights)


@pytest.mark.parametrize("args", [(-1, [1]), (5, [0, 0]), (5, [1, -1])])
def test_allocate_refuses_nothing_to_allocate_over(args):
    with pytest.raises(ValueError):
        allocate(*args)


def test_round_div_is_half_away_from_zero():
    assert [round_div(n, 2) for n in (5, -5, 4, -4, 1, -1)] == [3, -3, 2, -2, 1, -1]
    assert round_div(7, 3) == 2 and round_div(-7, 3) == -2 and round_div(0, 5) == 0


# catalog and pricing: the charge for a line


@pytest.mark.parametrize(
    ("tenths", "per", "cents"),
    [(125, 1, 13), (-125, 1, -13), (-375, 1, -38), (5, 1, 1), (-5, 1, -1), (113750, 100, 114)],
)
def test_amount_cents_rounds_half_away_from_zero(tenths, per, cents):
    assert amount_cents(tenths, per) == cents


@pytest.mark.parametrize(
    ("sku", "quantity", "cents"),
    [
        ("BOLT", 250, 114),
        ("BOLT", 999, 455),
        ("BOLT", 1000, 455),
        ("BOLT", 1001, 455),
        ("BOLT", 1200, 535),
        ("CABLE", 49, 12226),
        ("CABLE", 50, 12451),
        ("CABLE", 60, 14701),
        ("MANUAL", 3, 5997),
    ],
)
def test_tiers_are_graduated(sku, quantity, cents):
    assert price_line(sku, quantity).gross_cents == cents


def test_a_credit_line_is_negative_and_rounded_half_away_from_zero():
    assert price_line("LOYALTY", 1).gross_cents == -13
    assert price_line("LOYALTY", 3).gross_cents == -38


def test_a_line_needs_a_positive_quantity_and_a_known_sku():
    with pytest.raises(ValueError):
        price_line("CABLE", 0)
    with pytest.raises(KeyError):
        price_line("NOPE", 1)


# invoices: tax per group, discount order and spread


def test_tax_is_computed_once_per_group_not_per_line():
    bolts = build_invoice([("BOLT", 300), ("BOLT", 300), ("BOLT", 300)])
    assert (bolts.subtotal_cents, bolts.tax_cents, bolts.total_cents) == (411, 34, 445)
    cables = build_invoice([("CABLE", 1), ("CABLE", 1), ("CABLE", 1)])
    assert (cables.subtotal_cents, cables.tax_cents, cables.total_cents) == (750, 62, 812)


def test_each_group_has_its_own_rate_and_its_own_rounding():
    invoice = build_invoice([("MANUAL", 1), ("CABLE", 1), ("CABLE", 1), ("SERVICE", 1)])
    assert invoice.subtotal_cents == 1999 + 250 + 250 + 7500
    assert invoice.tax_cents == 80 + 41 + 0
    assert invoice.total_cents == invoice.subtotal_cents + invoice.tax_cents


def test_tax_is_charged_on_the_amounts_after_the_discount():
    invoice = build_invoice([("MANUAL", 1)], [percent("10")])
    assert (invoice.discount_cents, invoice.tax_cents) == (200, 72)


@pytest.mark.parametrize("order", [0, 1])
def test_percentages_apply_before_fixed_amounts_whatever_the_list_order(order):
    discounts = [fixed(500), percent("10")]
    if order:
        discounts.reverse()
    invoice = build_invoice([("MANUAL", 5)], discounts)
    assert invoice.discount_cents == 1500
    assert (invoice.tax_cents, invoice.total_cents) == (340, 8835)


def test_two_percentages_apply_one_after_the_other():
    invoice = build_invoice([("MANUAL", 5)], [percent("10"), percent("10")])
    assert invoice.discount_cents == 1000 + 900  # 9995 -> 999.5 rounds to 1000, then 8995 -> 899.5


def test_a_discount_never_takes_off_more_than_is_left():
    invoice = build_invoice([("MANUAL", 1)], [fixed(5000)])
    assert invoice.discount_cents == 1999
    assert (invoice.tax_cents, invoice.total_cents) == (0, 0)
    both = build_invoice([("MANUAL", 1)], [percent("100"), fixed(300)])
    assert both.discount_cents == 1999


def test_the_discount_is_spread_with_the_largest_remainders_first():
    invoice = build_invoice([("BOLT", 100), ("CABLE", 1)], [fixed(10)])
    assert [item.discount_cents for item in invoice.lines] == [2, 8]
    assert [item.net_cents for item in invoice.lines] == [44, 242]
    assert sum(item.discount_cents for item in invoice.lines) == invoice.discount_cents


def test_a_credit_line_is_not_discounted_and_does_not_count_in_the_base():
    invoice = build_invoice([("CABLE", 1), ("LOYALTY", 1)], [fixed(50)])
    assert [item.discount_cents for item in invoice.lines] == [50, 0]
    assert invoice.discount_cents == 50
    assert invoice.tax_cents == 15  # (250 - 50 - 13) * 8.25% = 15.43
    only_credit = build_invoice([("LOYALTY", 3)], [percent("50")])
    assert only_credit.discount_cents == 0


def test_a_credit_only_invoice_has_negative_tax_and_total():
    invoice = build_invoice([("LOYALTY", 3)])
    assert (invoice.subtotal_cents, invoice.tax_cents, invoice.total_cents) == (-38, -3, -41)


def test_the_printed_figures_add_up():
    invoice = build_invoice(
        [("MANUAL", 1), ("BOLT", 100), ("CABLE", 1)], [fixed(10), percent("10")]
    )
    text = render(invoice)
    lines = text.splitlines()
    assert lines[:3] == ["Manual: 1 = $19.99", "Bolts: 100 = $0.46", "Cable: 1 = $2.50"]
    assert lines[3].startswith("Subtotal: ") and lines[4].startswith("Discount: -")
    assert invoice.subtotal_cents - invoice.discount_cents + invoice.tax_cents == invoice.total_cents
    assert sum(item.discount_cents for item in invoice.lines) == invoice.discount_cents
    assert not text.endswith("\n")
