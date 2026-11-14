"""Failure labels in the report: counts per side, README line, triage spend, --check."""

from pathlib import Path

import pytest
from report_support import SUN, edit_records, error_on, run_day, store
from runner_support import FakeGrader, build_cli
from triage_support import model_triage, verdict

from agent_lab.report.analysis import ResultsAnalyzer
from agent_lab.report.markdown import END, START, MarkdownRenderer
from agent_lab.runner.records import AttemptRecord
from agent_lab.runner.triage import FailureTriage
from agent_lab.runner.triage_rules import TriageRules

NO_VERIFICATION = {"no_verification"}


def failing_day(tmp: Path, **kwargs: object) -> Path:
    results = tmp / "results"
    run_day(tmp / "w", results, SUN, grader=FakeGrader(passed=False), **kwargs)
    return results


def analyze(results: Path):  # type: ignore[no-untyped-def]
    return ResultsAnalyzer(resamples=200).analyze(store(results))


def failures(results: Path):  # type: ignore[no-untyped-def]
    (track,) = analyze(results).tracks
    assert track.current.failures is not None
    return track.current.failures


def test_failed_attempts_are_counted_per_label_and_side(tmp_path: Path) -> None:
    f = failures(failing_day(tmp_path))
    for side in (f.baseline, f.variant):
        assert side.failed == 12 and side.labels == {"no_verification": 12}
        assert (side.model_labelled, side.unlabelled) == (0, 0)


def test_model_labels_and_old_unlabelled_failures_are_told_apart(tmp_path: Path) -> None:
    results = failing_day(tmp_path)

    def relabel(r: AttemptRecord) -> AttemptRecord:
        if r.config_role == "baseline":
            return r.model_copy(
                update={"triage_label": None, "triage_source": None, "triage_note": None}
            )
        if r.trial % 2:
            return r.model_copy(update={"triage_label": "incomplete_fix", "triage_source": "model"})
        return r

    edit_records(results, relabel)
    f = failures(results)
    assert (f.baseline.labels, f.baseline.unlabelled, f.baseline.model_labelled) == ({}, 12, 0)
    assert f.variant.model_labelled == 6 and f.variant.labels["incomplete_fix"] == 6
    assert sum(f.variant.labels.values()) == 12 and f.variant.unlabelled == 0


def test_only_included_pairs_are_counted(tmp_path: Path) -> None:
    results = failing_day(tmp_path, responder=error_on(3))
    data = analyze(results)
    series = data.tracks[0].current
    assert series.excluded.error == 1
    assert series.failures is not None
    assert series.failures.baseline.failed == series.pairs == series.failures.variant.failed


def test_passing_series_have_no_failure_stats(tmp_path: Path) -> None:
    results = tmp_path / "results"
    run_day(tmp_path / "w", results, SUN)
    assert analyze(results).tracks[0].current.failures is None


def render(results: Path) -> str:
    return MarkdownRenderer().render(analyze(results), {})


def test_the_readme_line_lists_labels_by_count_for_each_side(tmp_path: Path) -> None:
    text = render(failing_day(tmp_path))
    assert "Failures, advisory labels: baseline 12 failed: 12 no verification;" in text
    assert "variant 12 failed: 12 no verification" in text
    assert "Failure labels:" in text and "advisory" in text  # the caveat


def test_the_line_is_left_out_when_nothing_is_labelled(tmp_path: Path) -> None:
    results = failing_day(tmp_path)
    edit_records(
        results,
        lambda r: r.model_copy(
            update={"triage_label": None, "triage_source": None, "triage_note": None}
        ),
    )
    text = render(results)
    assert "Failures, advisory labels" not in text
    assert analyze(results).tracks[0].current.failures is not None  # summary.json keeps counts


def test_triage_spend_is_its_own_term_and_not_a_warm_up(tmp_path: Path) -> None:
    model, _ = model_triage([verdict("wrong_approach")] * 80)

    class ModelOnly(TriageRules):
        def classify(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return None

    results = failing_day(tmp_path, triage=FailureTriage(ModelOnly(), model))
    data = analyze(results)
    records, _ = store(results).read_all_attempts()
    (summary,), _ = store(results).read_summaries()
    assert data.spend_triage_usd > 0
    assert data.spend_triage_usd == pytest.approx(sum(r.triage_cost_usd for r in records))
    assert data.spend_warmups_usd == pytest.approx(summary.warmup_cost_usd)
    assert data.spend_usd == pytest.approx(summary.total_cost_usd)
    assert "+ triage $" in render(results)


def test_without_triage_spend_the_spend_line_is_unchanged(tmp_path: Path) -> None:
    text = render(failing_day(tmp_path))
    assert "triage $" not in text


def test_report_build_check_is_clean_after_a_build_with_labels(tmp_path: Path) -> None:
    results = failing_day(tmp_path / "r")
    root = tmp_path / "project"
    root.mkdir()
    readme = root / "README.md"
    readme.write_text(f"# T\n\n{START}\nplaceholder\n{END}\n")
    world = build_cli(root / "cli")
    argv = ["report", "build", "--results-dir", str(results), "--readme", str(readme)]
    argv += ["--out", str(root / "report")]
    assert world.cli.run(argv) == 0
    assert world.cli.run([*argv, "--check"]) == 0
    assert "no verification" in readme.read_text()
    assert '"labels"' in (root / "report" / "summary.json").read_text()
