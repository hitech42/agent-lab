from decimal import Decimal
from pathlib import Path

from ledger import data_lines, format_money, row_count, total_column

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "ledger.csv"


def write(tmp_path, text):
    path = tmp_path / "ledger.csv"
    path.write_text(text)
    return path


def test_total_is_exact(tmp_path):
    path = write(tmp_path, "date,description,amount\n2026-01-01,a,0.10\n2026-01-02,b,0.20\n")
    assert total_column(path) == Decimal("0.30")


def test_comment_and_blank_lines_are_skipped():
    assert list(data_lines(["# note\n", "\n", "a,b,c\n"])) == ["a,b,c\n"]


def test_returns_a_decimal(tmp_path):
    path = write(tmp_path, "date,description,amount\n2026-01-01,a,1.00\n")
    assert isinstance(total_column(path), Decimal)


def test_sample_ledger_total():
    assert total_column(SAMPLE) == Decimal("3415.99")


def test_format_money():
    assert format_money(Decimal("3415.9")) == "3,415.90"


def test_row_count_counts_transactions(tmp_path):
    path = write(tmp_path, "date,description,amount\n2026-01-01,a,1.00\n2026-01-02,b,2.00\n")
    assert row_count(path) == 2
