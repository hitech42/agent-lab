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
