"""The three renderers. Each takes sorted rows and the resolved column names."""

from columns import numeric_columns


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
    return text[: width - 1] + "\u2026" if len(text) > width else text


def _lines(lines):
    return "".join(line + "\n" for line in lines)


def format_csv(rows, columns, header):
    """CSV: ``None`` is empty, booleans ``true``/``false``, floats ``repr``, lists joined
    with ``"|"``; a cell is quoted (quotes doubled) when it contains a comma, a quote, a
    line break or leading or trailing whitespace. Every line ends with a newline.
    """
    lines = []
    if header:
        lines.append(",".join(_csv_quote(str(col)) for col in columns))
    for row in rows:
        lines.append(",".join(_csv_quote(_csv_value(row.get(col))) for col in columns))
    return _lines(lines)


def format_markdown(rows, columns, header, null, float_digits):
    """A Markdown table; ``header=False`` is a ``ValueError``."""
    if not header:
        raise ValueError("markdown tables need a header")
    numeric = numeric_columns(rows, columns)
    lines = ["| " + " | ".join(str(col) for col in columns) + " |"]
    lines.append("| " + " | ".join("---:" if col in numeric else "---" for col in columns) + " |")
    for row in rows:
        cells = []
        for col in columns:
            text = _display(row.get(col), null, float_digits)
            text = text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")
            cells.append(text.replace("\r", "<br>"))
        lines.append("| " + " | ".join(cells) + " |")
    return _lines(lines)


def format_text(rows, columns, max_width, header, null, float_digits):
    """Aligned text: cells cut to ``max_width``, numeric columns right-aligned."""
    numeric = numeric_columns(rows, columns)
    heads = [_truncate(str(col), max_width) for col in columns]
    body = []
    for row in rows:
        cells = []
        for col in columns:
            text = _display(row.get(col), null, float_digits)
            text = text.replace("\r", " ").replace("\n", " ")
            cells.append(_truncate(text, max_width))
        body.append(cells)
    widths = [max([len(heads[i])] + [len(cells[i]) for cells in body]) for i in range(len(columns))]

    def line_of(values):
        parts = []
        for index, value in enumerate(values):
            align = value.rjust if columns[index] in numeric else value.ljust
            parts.append(align(widths[index]))
        return "  ".join(parts).rstrip()

    lines = []
    if header:
        lines.append(line_of(heads))
        lines.append("  ".join("-" * width for width in widths).rstrip())
    lines.extend(line_of(cells) for cells in body)
    return _lines(lines)
