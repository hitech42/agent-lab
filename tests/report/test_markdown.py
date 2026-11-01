import re
from pathlib import Path

import pytest
from report_support import delta, realistic, series, side

from agent_lab.report.analysis import ResultsAnalyzer
from agent_lab.report.markdown import (
    END,
    START,
    MarkdownRenderer,
    ReadmeMarkersError,
    ReadmeUpdater,
    escape,
)
from agent_lab.report.model import Exclusions, ReportData, TrackReport
from agent_lab.runner.storage import ResultsStore

README = f"# Title\n\nintro text\n\n{START}\nold generated text\n{END}\n\n## License\n\nMIT\n"


def data(tracks: list[TrackReport] | None = None, **overrides) -> ReportData:  # type: ignore[no-untyped-def]
    values = {
        "latest_date": "2026-11-10",
        "days_run": 3,
        "attempts": 72,
        "spend_usd": 0.1,
        "spend_attempts_usd": 0.09,
        "spend_warmups_usd": 0.01,
        "unreadable": 0,
        "min_pairs": 10,
        "resamples": 2000,
        "cluster_min_tasks": 8,
        "comparisons_shown": 3,
        "comparisons_by_metric": {"cost": 1, "pass": 1, "turns": 1},
        "latest_day": "eval(tool-design): baseline 3/4 vs variant 4/4 passed, $0.0500 [completed]",
        "tracks": tracks
        if tracks is not None
        else [TrackReport(track="tool-design", current=series(), older=[])],
        "daily": [],
    }
    return ReportData(**{**values, **overrides})


def render(report: ReportData, charts: dict[str, str] | None = None) -> str:
    return MarkdownRenderer().render(report, charts or {})


def row_for(**overrides) -> str:  # type: ignore[no-untyped-def]
    """The table row of a series called "t" (the rest of the section is ignored)."""
    current = series(track="t", **overrides)
    text = render(data([TrackReport(track="t", current=current, older=[])]))
    return next(line for line in text.splitlines() if line.startswith("| t "))


# --- the marker replacement -----------------------------------------------------------


def test_only_the_text_between_the_markers_changes() -> None:
    updated = ReadmeUpdater().replace(README, "## Results\n\nnew text")
    assert updated.startswith(
        "# Title\n\nintro text\n\n" + START + "\n## Results\n\nnew text\n" + END
    )
    assert updated.endswith("\n\n## License\n\nMIT\n")
    assert "old generated text" not in updated


def test_replacement_is_idempotent() -> None:
    once = ReadmeUpdater().replace(README, "section")
    assert ReadmeUpdater().replace(once, "section") == once


def test_text_outside_the_markers_is_byte_for_byte_untouched() -> None:
    odd = f"héllo\r\n  trailing  \n{START}\nx\n{END}\n\n\n   indented\n"
    updated = ReadmeUpdater().replace(odd, "s")
    assert updated.startswith("héllo\r\n  trailing  \n") and updated.endswith(
        f"{END}\n\n\n   indented\n"
    )


@pytest.mark.parametrize(
    "readme",
    [
        "no markers at all",
        f"{START}\nonly start",
        f"only end\n{END}",
        f"{START}\na\n{END}\n{START}\nb\n{END}",
        f"{START}\n{START}\n{END}",
        f"{START}\n{END}\n{END}",
    ],
)
def test_missing_or_duplicate_markers_fail_clearly(readme: str) -> None:
    with pytest.raises(ReadmeMarkersError, match="exactly one"):
        ReadmeUpdater().replace(readme, "x")


def test_an_end_marker_before_the_start_marker_fails() -> None:
    with pytest.raises(ReadmeMarkersError, match="before"):
        ReadmeUpdater().replace(f"{END}\n{START}", "x")


# --- the section ----------------------------------------------------------------------


def test_the_section_states_the_data_range_and_the_totals() -> None:
    text = render(data())
    assert text.startswith("## Results")
    assert (
        "Data through **2026-11-10**" in text and "3 day(s) run" in text and "72 attempts" in text
    )
    assert "**$0.1000** total spend (attempts $0.0900 + warm-ups $0.0100)" in text
    assert "baseline 3/4 vs variant 4/4 passed" in text


def test_the_table_has_a_row_with_every_documented_column() -> None:
    text = render(data())
    header = next(line for line in text.splitlines() if line.startswith("| Track"))
    assert [c.strip() for c in header.strip("|").split("|")] == [
        "Track", "Pairs (excl.)", "Baseline pass", "Variant pass", "Δ pass (pp)",
        "Δ cost / attempt", "Δ turns", "Verdict", "Flags",
    ]  # fmt: skip
    row = next(line for line in text.splitlines() if line.startswith("| tool-design"))
    assert "24 (0)" in row and "50% [30\u201370]" in row and "75% [55\u201395]" in row
    assert "+25.0 [+5.0, +45.0]" in row and "+$0.0010 [+$0.0005, +$0.0015]" in row
    assert "+1.0 [+0.5, +1.5]" in row
    assert "pass: variant higher; cost: variant higher" in row


def test_excluded_pairs_are_shown_in_parentheses() -> None:
    assert "24 (3)" in row_for(excluded=Exclusions(error=2, tampered=1))


def test_insufficient_data_shows_the_point_estimate_without_an_interval() -> None:
    row = row_for(
        pairs=7, baseline=side(0.5, 7, ci=False), variant=side(1.0, 7, ci=False),
        delta_pass_pp=delta(50.0, None, "insufficient data"),
        delta_cost_usd=delta(0.001, None, "insufficient data"),
        delta_turns=delta(1.0, None, "insufficient data"),
    )  # fmt: skip
    assert "50% (insufficient data)" in row and "+50.0 (insufficient data)" in row
    assert "+$0.0010 (insufficient data)" in row and "[" not in row and "pass:" not in row


def test_zero_deltas_never_print_a_negative_zero() -> None:
    row = row_for(delta_pass_pp=delta(-0.0, 1.0), delta_cost_usd=delta(-0.00001, 0.00002))
    assert "+0.0 [-1.0, +1.0]" in row and "+$0.0000" in row
    assert not re.search(r"(?<![\[,] )-0\.0\b", row.replace("[-1.0", "["))


def test_flags_mark_the_ceiling_and_the_noise_floor_reference() -> None:
    both = series(track="aa-test", ceiling=True, noise_floor=True)
    text = render(data([TrackReport(track="aa-test", current=both, older=[])]))
    assert "ceiling, noise-floor reference" in text


def test_the_notes_list_the_question_the_change_and_cost_per_solved() -> None:
    text = render(data())
    note = next(line for line in text.splitlines() if line.startswith("- **tool-design**"))
    assert "tool\\_set: structured → bash\\_only" in note
    assert "$0.0060 → $0.0060" in note and "2 day(s), 3 task(s)" in note


def test_a_prompt_change_links_to_the_variants_config_file() -> None:
    changed = series(description="system prompt changed", variant_hash="c" * 12)
    text = render(data([TrackReport(track="t", current=changed, older=[])]))
    assert f"[system prompt changed](results/configs/{'c' * 12}.json)" in text


def test_a_missing_cost_per_solved_is_n_a() -> None:
    unsolved = series(baseline=side(0.0).model_copy(update={"cost_per_solved_usd": None}))
    assert "n/a → $0.0060" in render(data([TrackReport(track="t", current=unsolved, older=[])]))


def test_older_series_go_in_a_collapsed_block_after_the_current_ones() -> None:
    old = series(current=False, first_date="2026-10-01", last_date="2026-10-20")
    text = render(data([TrackReport(track="tool-design", current=series(), older=[old])]))
    assert text.index("| tool-design |") < text.index("<details>")
    assert "<details><summary>Older series (1)" in text and text.rstrip().count("</details>") == 1
    assert "tool-design (2026-10-01 → 2026-10-20)" in text


def test_chart_links_appear_only_for_charts_that_exist() -> None:
    with_charts = render(data(), {"Daily spend": "report/daily.svg"})
    assert "![Daily spend](report/daily.svg)" in with_charts and "tracks.svg" not in with_charts
    assert "![" not in render(data())


def test_the_caveats_cover_every_required_point() -> None:
    text = render(data())
    for needle in (
        "variant minus baseline", "neutral words", "fewer than 10 included pairs",
        "insufficient data",
        "Ceiling", "Independence", "task-clustered bootstrap", "at least 8 tasks",
        "Multiple comparisons", "3 comparisons are shown (cost: 1, pass: 1, turns: 1)",
        "1 in 20", "noise-floor reference and should show none", "Exclusions", "`error`",
        "environment-tampered", "count as failures unless the attempt passed", "Method:",
        "colour scheme",
    ):  # fmt: skip
        assert needle in text, needle


def test_unreadable_lines_appear_in_the_caveats_only_when_there_are_some() -> None:
    assert "unreadable" not in render(data())
    assert "5 unreadable result line(s)/file(s) were ignored" in render(data(unreadable=5))


def test_no_results_gives_a_placeholder_without_dates_or_counts() -> None:
    text = render(data([], latest_date=None, days_run=0, attempts=0))
    assert text.startswith("## Results") and "No results yet" in text
    assert not re.search(r"\d", text)


def test_data_without_complete_pairs_says_so() -> None:
    assert "No complete baseline/variant pairs yet." in render(data([]))


# --- untrusted text -------------------------------------------------------------------


HOSTILE = (
    "x\n# Heading <script>alert(1)</script> [click](http://evil.example) "
    "![i](http://e/x.png) | a | b `c` *d* _e_"
)


def test_escape_neutralizes_markup_links_html_and_line_breaks() -> None:
    out = escape(HOSTILE)
    assert "\n" not in out and "<" not in out and ">" not in out
    assert not re.search(r"(?<!\\)[\[\]()|`*_#!]", out)  # every special character is escaped
    assert "&lt;script&gt;" in out


def test_escape_bounds_the_length() -> None:
    assert len(escape("a" * 1000)) == 200 and len(escape("a" * 1000, 50)) == 50


def test_hostile_free_text_cannot_inject_markup_into_the_section() -> None:
    hostile = series(question=HOSTILE, description=HOSTILE, track="evil" + HOSTILE)
    text = render(data([TrackReport(track="t", current=hostile, older=[])]))
    assert "<script" not in text and "](http://evil.example)" not in text.replace("\\]\\(", "")
    assert "\n# Heading" not in text and "![i]" not in text.replace("\\!\\[i\\]", "")
    lines = text.splitlines()
    assert sum(1 for line in lines if line.startswith("|")) == 3  # header, divider, one row


# --- the real data --------------------------------------------------------------------


def test_the_section_for_a_realistic_results_directory(tmp_path: Path) -> None:
    results = realistic(tmp_path)
    report = ResultsAnalyzer(resamples=200).analyze(ResultsStore(results))
    text = render(report, {"Daily spend": "report/daily.svg"})
    for track in ("aa-test", "model-comparison", "tool-design"):
        assert f"| {track.replace('-', '-')} |" in text
    assert "11 (1)" in text  # the errored pair is excluded and counted
    assert (
        "<details>" in text
        and "turn-budget" not in text.split("**Caveats**")[0].split("| Track")[1]
    )
