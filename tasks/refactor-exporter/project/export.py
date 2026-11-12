"""Render rows (a list of dicts) as aligned text, CSV or a Markdown table.

``export`` is the library entry point and ``main`` the command line one (JSON rows on
standard input). Both are used by other tools, so their behaviour must not change.

Layout. The module is to be split into three, keeping ``export`` and ``main`` here:

- ``columns.py``: ``resolve_columns(rows, columns)`` returns the list of column names;
  ``sort_rows(rows, columns, sort_by, reverse)`` returns the sorted list of rows (the rows in
  their original order when ``sort_by`` is ``None``), does not modify the list it is given
  and raises the same ``ValueError`` as ``export`` for a ``sort_by`` that is not one of the
  columns; ``resolve_columns`` raises the one for duplicate names; and
  ``numeric_columns(rows, columns)`` returns the set of those columns that are numeric:
  every value that is not ``None`` (a missing key is ``None``) is a number, booleans are not
  numbers, and a column with no values is not numeric.
- ``formatters.py``: ``format_text(rows, columns, max_width, header, null, float_digits)``,
  ``format_csv(rows, columns, header)`` and ``format_markdown(rows, columns, header, null,
  float_digits)``. Each takes rows that are already sorted and the resolved list of column
  names, and returns the finished string exactly as ``export`` returns it today.
- Size: ``export.py`` has at most 90 lines of code. Blank lines, comments and docstrings do
  not count.
"""

import argparse
import json
import sys


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _display(value, null, digits):
    """A value as shown in the text and Markdown formats."""
    if value is None:
        return null
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return format(value, f".{digits}g")
    if isinstance(value, (list, tuple)):
        return "; ".join(_display(item, null, digits) for item in value)
    return str(value)


def _csv_value(value):
    """A value as written in CSV: no ``null`` text, booleans in lower case, full floats."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "|".join(_csv_value(item) for item in value)
    return str(value)


def _csv_quote(text):
    if any(ch in text for ch in ',"\r\n') or text != text.strip():
        return '"' + text.replace('"', '""') + '"'
    return text


def _truncate(text, width):
    if len(text) > width:
        return text[: width - 1] + "\u2026"
    return text


def export(
    rows,
    fmt="text",
    columns=None,
    sort_by=None,
    reverse=False,
    max_width=20,
    header=True,
    null="",
    float_digits=6,
):
    """Render ``rows`` (a list of dicts) as a string in ``fmt``: text, csv or markdown.

    Columns: ``columns`` lists the columns to show, in order; by default they are all the
    keys of all rows, in the order they first appear. Duplicate names in ``columns`` are a
    ``ValueError``. A row without a key shows an empty cell. With no columns the result is
    an empty string.

    Sorting: ``sort_by`` names a column (``ValueError`` if it is not one). Numbers sort
    before everything else; other values (booleans included) sort by their ``str``. Rows
    where the value is missing or ``None`` always come last, whatever ``reverse`` says.
    The sort is stable, ``reverse`` included.

    Values: in text and Markdown ``None`` shows as ``null``, booleans as ``yes``/``no``,
    floats with ``float_digits`` significant digits (``3.0`` is ``3``), lists joined with
    ``"; "``. In CSV ``None`` is empty, booleans are ``true``/``false``, floats use
    ``repr``, lists are joined with ``"|"``, and a cell is quoted (quotes doubled) when it
    contains a comma, a quote, a line break or leading or trailing whitespace.

    Text: cells longer than ``max_width`` are cut to ``max_width - 1`` characters plus
    ``"\u2026"`` (headers too; ``max_width`` below 2 is a ``ValueError``), line breaks in
    cells become spaces, columns are as wide as their longest cell and separated by two
    spaces, a column of only numbers (booleans are not numbers) is right-aligned, header
    included, the header is followed by a line of dashes, and trailing spaces are removed
    from every line. ``header=False`` leaves out the header and the dashes.

    Markdown: ``| a | b |`` rows, a separator row with ``---`` (``---:`` for a numeric
    column), ``|`` in a cell written as ``\\|`` and line breaks as ``<br>``; no cutting and
    no padding. ``header=False`` is a ``ValueError`` ("markdown tables need a header").

    CSV: one line per row, ``header=False`` leaves out the header line. Every line,
    the last included, ends with a newline, and so does every text and Markdown result.
    An unknown ``fmt`` is a ``ValueError``.
    """
    if fmt not in ("text", "csv", "markdown"):
        raise ValueError(f"unknown format {fmt!r}; expected text, csv or markdown")
    if max_width < 2:
        raise ValueError("max_width must be at least 2")
    if fmt == "markdown" and not header:
        raise ValueError("markdown tables need a header")
    if columns is None:
        cols = []
        for row in rows:
            for key in row:
                if key not in cols:
                    cols.append(key)
    else:
        cols = list(columns)
        if len(set(cols)) != len(cols):
            raise ValueError("duplicate column names")
    if sort_by is not None:
        if sort_by not in cols:
            raise ValueError(f"cannot sort by unknown column {sort_by!r}")
        present = []
        missing = []
        for row in rows:
            if row.get(sort_by) is None:
                missing.append(row)
            else:
                present.append(row)

        def sort_key(row):
            value = row[sort_by]
            if _is_number(value):
                return (0, value, "")
            return (1, 0, str(value))

        present.sort(key=sort_key, reverse=reverse)
        rows = present + missing
    if not cols:
        return ""
    numeric = {}
    for col in cols:
        values = [row.get(col) for row in rows if row.get(col) is not None]
        numeric[col] = bool(values) and all(_is_number(value) for value in values)
    if fmt == "csv":
        lines = []
        if header:
            lines.append(",".join(_csv_quote(str(col)) for col in cols))
        for row in rows:
            lines.append(",".join(_csv_quote(_csv_value(row.get(col))) for col in cols))
        return "".join(line + "\n" for line in lines)
    if fmt == "markdown":
        lines = ["| " + " | ".join(str(col) for col in cols) + " |"]
        lines.append("| " + " | ".join("---:" if numeric[col] else "---" for col in cols) + " |")
        for row in rows:
            cells = []
            for col in cols:
                text = _display(row.get(col), null, float_digits)
                text = text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")
                cells.append(text.replace("\r", "<br>"))
            lines.append("| " + " | ".join(cells) + " |")
        return "".join(line + "\n" for line in lines)
    heads = [_truncate(str(col), max_width) for col in cols]
    body = []
    for row in rows:
        cells = []
        for col in cols:
            text = _display(row.get(col), null, float_digits)
            text = text.replace("\r", " ").replace("\n", " ")
            cells.append(_truncate(text, max_width))
        body.append(cells)
    widths = []
    for index in range(len(cols)):
        widths.append(max([len(heads[index])] + [len(cells[index]) for cells in body]))

    def line_of(values):
        parts = []
        for index, value in enumerate(values):
            if numeric[cols[index]]:
                parts.append(value.rjust(widths[index]))
            else:
                parts.append(value.ljust(widths[index]))
        return "  ".join(parts).rstrip()

    lines = []
    if header:
        lines.append(line_of(heads))
        lines.append("  ".join("-" * width for width in widths).rstrip())
    for cells in body:
        lines.append(line_of(cells))
    return "".join(line + "\n" for line in lines)


def main(argv, stdin=None, stdout=None, stderr=None):
    """Command line entry point: read a JSON list of objects, print it with ``export``.

    Options: ``--format/-f`` (text, csv, markdown), ``--columns a,b``, ``--sort COLUMN``,
    ``--reverse``, ``--max-width N``, ``--no-header`` and ``--null TEXT``. Returns the exit
    status: 0 on success, 2 for a usage error (as ``argparse`` reports it), 1 when the
    input is not a JSON list of objects or ``export`` raises ``ValueError``; those errors
    print ``error: <message>`` to ``stderr`` and nothing to ``stdout``.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = argparse.ArgumentParser(prog="export")
    parser.add_argument("--format", "-f", choices=["text", "csv", "markdown"], default="text")
    parser.add_argument("--columns")
    parser.add_argument("--sort")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--max-width", type=int, default=20)
    parser.add_argument("--no-header", action="store_true")
    parser.add_argument("--null", default="")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        rows = json.load(stdin)
    except ValueError as exc:
        print(f"error: input is not valid JSON ({exc})", file=stderr)
        return 1
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        print("error: input must be a JSON list of objects", file=stderr)
        return 1
    columns = args.columns.split(",") if args.columns else None
    try:
        text = export(
            rows,
            args.format,
            columns,
            args.sort,
            args.reverse,
            args.max_width,
            not args.no_header,
            args.null,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=stderr)
        return 1
    stdout.write(text)
    return 0
