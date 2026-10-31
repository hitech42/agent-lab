from decimal import Decimal

import pytest
from ledger import total_column


def write(tmp_path, text):
    path = tmp_path / "ledger.csv"
    path.write_text(text)
    return path


def test_many_small_amounts_are_exact(tmp_path):
    rows = "".join(f"2026-01-01,item,0.01\n" for _ in range(1000))
    path = write(tmp_path, "date,description,amount\n" + rows)
    assert total_column(path) == Decimal("10.00")


def test_header_only_file_totals_zero(tmp_path):
    path = write(tmp_path, "date,description,amount\n")
    assert total_column(path) == Decimal("0")


def test_header_with_comments_and_blank_lines_only(tmp_path):
    path = write(tmp_path, "# exported\n\ndate,description,amount\n\n")
    assert total_column(path) == Decimal("0")


def test_column_is_chosen_by_header_name_not_position(tmp_path):
    path = write(tmp_path, "amount,date,description\n5.10,2026-01-01,a\n0.20,2026-01-02,b\n")
    assert total_column(path) == Decimal("5.30")


def test_other_column_by_name(tmp_path):
    path = write(tmp_path, "date,fee,amount\n2026-01-01,0.07,10.00\n2026-01-02,0.08,20.00\n")
    assert total_column(path, "fee") == Decimal("0.15")
    assert total_column(path, "amount") == Decimal("30.00")


def test_first_data_row_is_not_swallowed(tmp_path):
    path = write(tmp_path, "date,description,amount\n2026-01-01,a,100.00\n")
    assert total_column(path) == Decimal("100.00")


def test_quoted_thousands_separators(tmp_path):
    text = 'date,description,amount\n2026-01-01,a,"1,200.50"\n2026-01-02,b,"2,100.00"\n'
    assert total_column(write(tmp_path, text)) == Decimal("3300.50")


def test_negative_amounts_are_refunds(tmp_path):
    text = "date,description,amount\n2026-01-01,a,20.00\n2026-01-02,refund,-8.15\n"
    assert total_column(write(tmp_path, text)) == Decimal("11.85")


@pytest.mark.parametrize("bad", ["abc", "", "12.3.4", "nan", "inf"])
def test_unparseable_amount_raises_value_error(tmp_path, bad):
    path = write(tmp_path, f"date,description,amount\n2026-01-01,a,{bad}\n2026-01-02,b,1.00\n")
    with pytest.raises(ValueError):
        total_column(path)


def test_returns_decimal_with_exact_value(tmp_path):
    path = write(tmp_path, "date,description,amount\n2026-01-01,a,0.10\n2026-01-02,b,0.20\n")
    result = total_column(path)
    assert isinstance(result, Decimal)
    assert result == Decimal("0.30")


def test_comment_lines_between_rows_are_skipped(tmp_path):
    text = "# top\ndate,description,amount\n2026-01-01,a,1.50\n# middle\n2026-01-02,b,2.25\n"
    assert total_column(write(tmp_path, text)) == Decimal("3.75")
