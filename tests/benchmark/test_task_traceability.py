"""Pin the places where hidden-test requirements are stated (fast, no subprocesses).

Hidden tests must check stated behavior only: each requirement below used to be missing
from what the agent can read and was added to the prompt or to a project docstring.
"""

from task_builders import real_task


def project_source(task_id: str, name: str) -> str:
    text = (real_task(task_id).directory / "project" / name).read_text()
    return " ".join(text.split())  # docstrings wrap lines; compare on normalized whitespace


def test_csv_total_states_that_amounts_must_be_finite_decimals() -> None:
    doc = project_source("fix-csv-total", "ledger.py")
    for phrase in (
        "finite decimal number",
        "empty amount",
        "NaN or Infinity",
        "``nan``",
        "``inf``",
    ):
        assert phrase in doc


def test_csv_total_prompt_gives_no_extra_hint_about_nan_or_infinity() -> None:
    prompt = real_task("fix-csv-total").prompt.lower()
    assert "nan" not in prompt and "infinity" not in prompt and "finite" not in prompt


def test_date_range_states_whitespace_handling_and_days_in_range_errors() -> None:
    doc = project_source("fix-date-range", "daterange.py")
    assert "Leading and trailing whitespace around the whole text is ignored" in doc
    assert "Raises ``ValueError`` exactly when ``parse_range`` does" in doc


def test_top_flag_prompt_states_input_scope_empty_input_and_usage_errors() -> None:
    prompt = " ".join(real_task("add-top-flag").prompt.split())
    for phrase in (
        "all the input together",
        "even none at all",
        "exit with status 0",
        "non-integers such as 1.5",
        "a missing value",
        "exit with status 2",
    ):
        assert phrase in prompt


def test_invoice_rounding_is_stated_in_the_docstrings() -> None:
    money = project_source("fix-invoice-rounding", "money.py")
    for phrase in ("rounds away from zero", "exact for any amount", '"0.125"'):
        assert phrase in money
    invoice = project_source("fix-invoice-rounding", "invoice.py")
    for phrase in (
        "rounded once, after multiplying",
        "A negative unit price is a credit",
        "tax is computed on the subtotal after the discount",
        "Discount: -<amount>",
        "no trailing newline",
    ):
        assert phrase in invoice
    assert "computed once, on the whole subtotal" in project_source(
        "fix-invoice-rounding", "tax.py"
    )
    assert "rounded like every other amount" in project_source(
        "fix-invoice-rounding", "discount.py"
    )


def test_leaky_registry_states_isolation_and_idempotence() -> None:
    doc = project_source("fix-leaky-registry", "jobs.py")
    for phrase in (
        "The caller owns the result and may modify it",
        "never modified, and no two jobs share a tags list",
        "Each job has its own schedule, even when jobs share an interval",
        "Every scheduler has its own jobs",
        "same priority are in the order they were added",
        "calling it twice with the same ``now`` gives the same answer",
    ):
        assert phrase in doc


def test_unicode_usernames_states_the_matching_rule_and_every_edge() -> None:
    doc = project_source("unicode-usernames", "directory.py")
    for phrase in (
        "NFKC",
        "``str.casefold`` (not ``str.lower``)",
        "normalized to NFKC again",
        "Never raises: an empty or blank name matches nobody",
        "A user may be renamed to another spelling of the same name",
        "When it raises, nothing changes",
        "sorted by key (plain code point order)",
        "An empty or blank prefix matches every user",
    ):
        assert phrase in doc


def test_refactor_exporter_prompt_states_the_new_modules_and_the_size_cap() -> None:
    prompt = " ".join(real_task("refactor-exporter").prompt.split())
    for phrase in (
        "resolve_columns(rows, columns)",
        "sort_rows(rows, columns, sort_by, reverse)",
        "format_text(rows, columns, max_width, header, null, float_digits)",
        "format_csv(rows, columns, header)",
        "format_markdown(rows, columns, header, null, float_digits)",
        "at most 90 lines of code",
        "when `sort_by` is `None`",
    ):
        assert phrase in prompt


def test_settings_merge_states_removal_isolation_naming_and_conversion() -> None:
    doc = project_source("fix-settings-merge", "settings.py")
    for phrase in (
        "A value of ``None`` in ``override`` removes the key",
        "shares no dict or list with either input",
        "split on a double underscore",
        "a single underscore stays part of a name",
        "a whole number with an optional leading minus sign becomes an ``int``",
        "``true`` and ``false`` in any case become booleans",
        "the environment (see ``parse_env``) wins over everything",
    ):
        assert phrase in doc
