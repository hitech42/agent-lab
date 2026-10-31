from datetime import date

import pytest
from daterange import days_in_range, inclusive_days, overlaps, parse_range


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-01-28..2026-02-03", 7),
        ("2026-02-28..2026-03-01", 2),
        ("2024-02-28..2024-03-01", 3),
        ("2100-02-28..2100-03-01", 2),
        ("2000-02-28..2000-03-01", 3),
        ("2025-12-30..2026-01-02", 4),
        ("2026-01-01..2026-12-31", 365),
        ("2024-01-01..2024-12-31", 366),
    ],
)
def test_inclusive_counts(text, expected):
    assert days_in_range(text) == expected


def test_single_day_range_is_one_day():
    assert days_in_range("2026-05-05..2026-05-05") == 1
    assert inclusive_days(date(2026, 5, 5), date(2026, 5, 5)) == 1


def test_parse_tolerates_surrounding_whitespace():
    assert parse_range("  2026-01-01..2026-01-02 ") == (date(2026, 1, 1), date(2026, 1, 2))


def test_reversed_range_raises_value_error():
    with pytest.raises(ValueError):
        days_in_range("2026-02-03..2026-01-28")
    with pytest.raises(ValueError):
        parse_range("2026-02-03..2026-01-28")


@pytest.mark.parametrize(
    "text",
    ["", "2026-01-28", "2026-01-28..", "..2026-01-28", "2026-01-28..2026-02-03..2026-02-05",
     "2026-01-28..not-a-date", "2026-13-01..2026-13-02", "2026-02-30..2026-03-01"],
)
def test_malformed_input_raises_value_error(text):
    with pytest.raises(ValueError):
        parse_range(text)
    with pytest.raises(ValueError):
        days_in_range(text)


def test_overlaps_with_single_day_ranges():
    assert overlaps("2026-01-05..2026-01-05", "2026-01-05..2026-01-05")
    assert not overlaps("2026-01-05..2026-01-05", "2026-01-06..2026-01-06")
