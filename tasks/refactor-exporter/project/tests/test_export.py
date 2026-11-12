import io
import json

import pytest
from export import export, main

ROWS = [
    {"name": "Alice", "age": 30, "active": True},
    {"name": "Bob", "age": 25, "active": False},
]


def test_text():
    assert export(ROWS) == (
        "name   age  active\n"
        "-----  ---  ------\n"
        "Alice   30  yes\n"
        "Bob     25  no\n"
    )


def test_csv_and_markdown():
    assert export(ROWS, fmt="csv") == "name,age,active\nAlice,30,true\nBob,25,false\n"
    assert export(ROWS, fmt="markdown").splitlines()[1] == "| --- | ---: | --- |"


def test_sorting_and_columns():
    assert export(ROWS, fmt="csv", columns=["name"], sort_by="name", reverse=True) == (
        "name\nBob\nAlice\n"
    )


def test_errors():
    with pytest.raises(ValueError):
        export(ROWS, fmt="xml")
    with pytest.raises(ValueError):
        export(ROWS, sort_by="nope")


def test_main_prints_the_table():
    stdout = io.StringIO()
    assert main(["-f", "csv"], io.StringIO(json.dumps(ROWS)), stdout, io.StringIO()) == 0
    assert stdout.getvalue().startswith("name,age,active\n")


def test_the_pieces_are_split_into_modules():
    from columns import numeric_columns, resolve_columns, sort_rows
    from formatters import format_csv, format_markdown, format_text

    names = resolve_columns(ROWS, None)
    assert numeric_columns(ROWS, names) == {"age"}
    rows = sort_rows(ROWS, names, "age", False)
    assert [row["name"] for row in rows] == ["Bob", "Alice"]
    assert format_csv(rows, columns=names, header=True).startswith("name,age,active\nBob")
    assert format_markdown(
        rows, columns=names, header=True, null="", float_digits=6
    ).startswith("| name |")
    assert format_text(
        rows, columns=names, max_width=20, header=True, null="", float_digits=6
    ).startswith("name")
