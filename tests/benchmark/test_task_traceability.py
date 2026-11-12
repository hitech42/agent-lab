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


def test_invoice_rules_live_in_the_modules_that_own_them() -> None:
    def source(name: str) -> str:
        return project_source("fix-invoice-rounding", name)

    money = source("money.py")
    for phrase in (  # round_div, to_cents, amount_cents, price_line of a credit
        "rounded once, at the end of the computation that produces it",
        "rounds away from zero: 0.5 cents is 1 cent, -0.5 is -1, 2.5 is 3 and -2.5 is -3",
        "``round_div(5, 2)`` is 3 and ``round_div(-5, 2)`` is -3",
        "the cents left over go one each to the shares with the largest fractional remainders, "
        "a tie going to the earlier share",  # allocate
        "always add up to the total exactly",
        "``total_cents`` must not be negative, the weights must not be negative and must not "
        "all be zero; otherwise ``ValueError``",
    ):
        assert phrase in money, phrase
    catalog = source("catalog.py")
    for phrase in (  # tiers, credits, the charge for a line
        "A negative price is a credit",
        "Prices are graduated",
        "units 1 to 999 are charged the first price and unit 1000 and every unit after it the "
        "second",
        "``total_tenths / (10 * per)``, rounded as described in ``money``",
    ):
        assert phrase in catalog, phrase
    discounts = source("discounts.py")
    for phrase in (  # order, cap, base, spread, credit lines
        "every percentage first, each on what the earlier ones left, then every fixed amount",
        "whatever the order of the list",
        "never takes off more than what is left",
        "The base is the sum of the positive line amounts; a credit line is not discounted",
        "spread over its lines with ``money.allocate``, using the line amounts as weights",
        "a credit line weighs 0 and gets no discount",
    ):
        assert phrase in discounts, phrase
    tax = source("tax.py")
    for phrase in (  # per group, once, after the discount, credits
        "Tax is charged per group",
        "each after its share of the discount",
        "the tax is computed once on that sum and rounded once",
        "never computed line by line",
        "The invoice's tax is the sum of its groups' taxes",
        "A group whose sum is negative (a credit) has negative tax",
    ):
        assert phrase in tax, phrase
    pricing = source("pricing.py")
    assert "``quantity`` must be positive; otherwise ``ValueError``" in pricing
    invoice = source("invoice.py")
    for phrase in (
        "Subtotal minus discount plus tax",
        "Discount: -<amount>",
        "no trailing newline",
    ):
        assert phrase in invoice, phrase
    assert "The printed figures always add up" in invoice


def test_invoice_prompt_points_at_the_docstrings_and_names_no_symptom_or_rule() -> None:
    prompt = " ".join(real_task("fix-invoice-rounding").prompt.split()).lower()
    assert "docstrings" in prompt
    for hint in (
        "cent",
        "float",
        "wrong",
        "run the code",
        "tier",
        "graduated",
        "remainder",
        "per group",
        "half",
        "order",
        "credit",
    ):
        assert hint not in prompt, hint


def test_leaky_registry_states_isolation_and_idempotence() -> None:
    doc = project_source("fix-leaky-registry", "jobs.py")
    for phrase in (
        "The caller owns the result and may modify it",
        "never modified, and no two jobs share a tags list",
        "Each job has its own schedule, even when jobs share an interval",
        "Every scheduler has its own jobs",
        "same priority are in the order they were added",
        "Every scheduler has its own jobs and its own history",
        "``history`` lists the names of the jobs this scheduler has added, in order",
        "calling it twice with the same ``now`` gives the same answer",
    ):
        assert phrase in doc


def test_leaky_registry_prompt_leaves_the_interval_symptom_and_history_unmentioned() -> None:
    prompt = " ".join(real_task("fix-leaky-registry").prompt.split()).lower()
    assert "same interval" not in prompt and "history" not in prompt and "cache" not in prompt


def test_unicode_usernames_states_the_matching_rule_and_every_edge() -> None:
    doc = project_source("unicode-usernames", "directory.py")
    for phrase in (
        "NFKC",
        "``str.casefold`` (not ``str.lower``)",
        "it is NFKC-normalized and",
        "``canonical(canonical(x)) == canonical(x)`` for every name ``x``",
        '``"J"`` followed by a combining caron and the single character ``"ǰ"``',
        "Add every name, in order, and return their display names; or add none",
        "an earlier name in ``names`` (in whatever spelling)",
        "When it raises, the directory is unchanged",
        "A blank ``old`` matches nobody, so it raises ``KeyError``",
        "a blank ``new`` raises ``ValueError``",
        "Never raises: an empty or blank name matches nobody",
        "A user may be renamed to another spelling of the same name",
        "When it raises, nothing changes",
        "sorted by key (plain code point order)",
        "An empty or blank prefix matches every user",
    ):
        assert phrase in doc


def test_unicode_usernames_prompt_names_no_symptom_and_does_not_spell_out_the_recipe() -> None:
    prompt = " ".join(real_task("unicode-usernames").prompt.split()).lower()
    for hint in ("keyboard", "capital", "lookup", "second account", "nfkc", "casefold"):
        assert hint not in prompt, hint
    doc = project_source("unicode-usernames", "directory.py")
    assert "normalized to NFKC again" not in doc and "denormalized" not in doc


def test_refactor_exporter_module_docstring_states_the_new_modules_and_the_size_cap() -> None:
    doc = project_source("refactor-exporter", "export.py")
    for phrase in (
        "``resolve_columns(rows, columns)`` returns the list of column names",
        "``sort_rows(rows, columns, sort_by, reverse)`` returns the sorted list of rows",
        "(the rows in their original order when ``sort_by`` is ``None``)",
        "does not modify the list it is given",
        "raises the same ``ValueError`` as ``export`` for a ``sort_by`` that is not one of "
        "the columns",
        "``resolve_columns`` raises the one for duplicate names",
        "``numeric_columns(rows, columns)`` returns the set of those columns that are numeric",
        "booleans are not numbers",
        "a column with no values is not numeric",
        "``format_text(rows, columns, max_width, header, null, float_digits)``",
        "``format_csv(rows, columns, header)``",
        "``format_markdown(rows, columns, header, null, float_digits)``",
        "returns the finished string exactly as ``export`` returns it today",
        "``export.py`` has at most 90 lines of code",
    ):
        assert phrase in doc, phrase


def test_refactor_exporter_prompt_points_at_the_docstring_and_lists_no_signatures() -> None:
    prompt = " ".join(real_task("refactor-exporter").prompt.split())
    assert "as its module docstring describes" in prompt
    for detail in ("resolve_columns", "format_text", "numeric_columns", "90", "80", "30"):
        assert detail not in prompt, detail
    assert "The code as it is today is the specification" in prompt
    assert "error messages and exit codes included" in prompt


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
