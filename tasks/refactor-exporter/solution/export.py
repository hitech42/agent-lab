"""Render rows (a list of dicts) as aligned text, CSV or a Markdown table.

``export`` is the library entry point and ``main`` the command line one (JSON rows on
standard input). Both are used by other tools, so their behaviour must not change.
"""

import argparse
import json
import sys

from columns import resolve_columns, sort_rows
from formatters import format_csv, format_markdown, format_text


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

    See ``columns.resolve_columns`` and ``columns.sort_rows`` for columns and sorting, and
    ``formatters`` for each format. With no columns the result is an empty string; an unknown
    ``fmt`` and a ``max_width`` below 2 are a ``ValueError``.
    """
    if fmt not in ("text", "csv", "markdown"):
        raise ValueError(f"unknown format {fmt!r}; expected text, csv or markdown")
    if max_width < 2:
        raise ValueError("max_width must be at least 2")
    if fmt == "markdown" and not header:
        raise ValueError("markdown tables need a header")
    names = resolve_columns(rows, columns)
    rows = sort_rows(rows, names, sort_by, reverse)
    if not names:
        return ""
    if fmt == "csv":
        return format_csv(rows, names, header)
    if fmt == "markdown":
        return format_markdown(rows, names, header, null, float_digits)
    return format_text(rows, names, max_width, header, null, float_digits)


def _parser():
    parser = argparse.ArgumentParser(prog="export")
    parser.add_argument("--format", "-f", choices=["text", "csv", "markdown"], default="text")
    parser.add_argument("--columns")
    parser.add_argument("--sort")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--max-width", type=int, default=20)
    parser.add_argument("--no-header", action="store_true")
    parser.add_argument("--null", default="")
    return parser


def _read_rows(stdin, stderr):
    """The JSON rows from ``stdin``, or ``None`` after printing why they are not usable."""
    try:
        rows = json.load(stdin)
    except ValueError as exc:
        print(f"error: input is not valid JSON ({exc})", file=stderr)
        return None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        print("error: input must be a JSON list of objects", file=stderr)
        return None
    return rows


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
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    rows = _read_rows(stdin, stderr)
    if rows is None:
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
