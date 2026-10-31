from datetime import date

from daterange import (
    days_in_range,
    format_range,
    inclusive_days,
    iter_days,
    overlaps,
    parse_range,
    split_range,
    weekdays_in_range,
)


def test_parse_range():
    assert parse_range("2026-01-28..2026-02-03") == (date(2026, 1, 28), date(2026, 2, 3))


def test_week_counts_both_ends():
    assert days_in_range("2026-03-01..2026-03-07") == 7


def test_inclusive_days_across_months():
    assert inclusive_days(date(2026, 1, 28), date(2026, 2, 3)) == 7


def test_overlaps():
    assert overlaps("2026-01-01..2026-01-10", "2026-01-10..2026-01-12")
    assert not overlaps("2026-01-01..2026-01-09", "2026-01-10..2026-01-12")


def test_iter_days_lists_every_day():
    days = list(iter_days("2026-01-30..2026-02-02"))
    assert days == [date(2026, 1, 30), date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 2)]


def test_weekdays_in_range():
    assert weekdays_in_range("2026-03-02..2026-03-08") == 5


def test_format_range_round_trips():
    start, end = date(2026, 1, 28), date(2026, 2, 3)
    assert parse_range(format_range(start, end)) == (start, end)


def test_split_range():
    assert split_range("2026-01-01..2026-01-05", 2) == [
        "2026-01-01..2026-01-02",
        "2026-01-03..2026-01-04",
        "2026-01-05..2026-01-05",
    ]
