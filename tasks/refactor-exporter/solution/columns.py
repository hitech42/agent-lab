"""Which columns to show, which are numeric, and in what order the rows go."""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def resolve_columns(rows, columns):
    """The column names to show: ``columns`` as given, else every key in order of appearance.

    Duplicate names in ``columns`` are a ``ValueError``.
    """
    if columns is None:
        names = []
        for row in rows:
            for key in row:
                if key not in names:
                    names.append(key)
        return names
    names = list(columns)
    if len(set(names)) != len(names):
        raise ValueError("duplicate column names")
    return names


def numeric_columns(rows, columns):
    """The columns that are numeric: every value that is not ``None`` is a number, and one is."""
    numeric = set()
    for col in columns:
        values = [row.get(col) for row in rows if row.get(col) is not None]
        if values and all(_is_number(value) for value in values):
            numeric.add(col)
    return numeric


def sort_rows(rows, columns, sort_by, reverse):
    """The rows sorted by the ``sort_by`` column (the rows unchanged if it is ``None``).

    Numbers sort before everything else; other values (booleans included) sort by their
    ``str``. Rows where the value is missing or ``None`` always come last, whatever
    ``reverse`` says. The sort is stable, ``reverse`` included. A ``sort_by`` that is not
    one of ``columns`` is a ``ValueError``.
    """
    if sort_by is None:
        return rows
    if sort_by not in columns:
        raise ValueError(f"cannot sort by unknown column {sort_by!r}")
    present = [row for row in rows if row.get(sort_by) is not None]
    missing = [row for row in rows if row.get(sort_by) is None]

    def sort_key(row):
        value = row[sort_by]
        return (0, value, "") if _is_number(value) else (1, 0, str(value))

    present.sort(key=sort_key, reverse=reverse)
    return present + missing
