from pathlib import Path

import pytest
from runner_support import MONDAY, TASKS_DIR, build_cli

from agent_lab.runner.calibration import CalibrationReport
from agent_lab.runner.records import AttemptRecord
from agent_lab.runner.run_command import RunArgs
from agent_lab.runner.storage import ResultsStore


def baseline_records(
    tmp_path: Path, model: str | None = None, **kwargs: object
) -> list[AttemptRecord]:
    """What scripts/calibrate.py would leave behind, written by the real runner with fakes."""
    world = build_cli(tmp_path)
    args = RunArgs(
        date=kwargs.pop("date", MONDAY),  # type: ignore[arg-type]
        track="aa-test",
        trials=kwargs.pop("trials", 2),  # type: ignore[arg-type]
        results_dir=world.results_dir,
        transcripts_dir=tmp_path / "t",
        tasks_dir=TASKS_DIR,
        schedule=Path(__file__).parents[2] / "experiments" / "schedule.yaml",
        baseline_only=True,
        model=model,
    )
    assert world.command.execute(args) == 0
    return ResultsStore(world.results_dir).read_all_attempts()[0]


def retier(records: list[AttemptRecord], tiers: dict[str, str], passes: dict[str, int]) -> list:
    """Set each task's difficulty and make only its first N attempts pass."""
    seen: dict[str, int] = {}
    out = []
    for record in records:
        n = seen.get(record.task_id, 0)
        seen[record.task_id] = n + 1
        out.append(
            record.model_copy(
                update={
                    "task_difficulty": tiers[record.task_id],
                    "passed": n < passes[record.task_id],
                }
            )
        )
    return out


TIERS = {"add-top-flag": "easy", "fix-csv-total": "medium", "fix-date-range": "hard"}


def test_a_baseline_run_gives_per_task_rates_turns_and_cost(tmp_path: Path) -> None:
    report = CalibrationReport.from_records(baseline_records(tmp_path, trials=2))
    (config,) = report.configs
    assert config.model == "claude-haiku-5-5" and config.effort == "low"
    assert [t.task_id for t in config.tasks] == ["add-top-flag", "fix-csv-total", "fix-date-range"]
    assert all(t.trials == 2 and t.passed == 2 and t.errors == 0 for t in config.tasks)
    assert all(t.mean_turns > 0 and t.mean_cost_usd > 0 for t in config.tasks)
    assert config.totals() == (6, 6)
    text = report.render()
    assert "claude-haiku-5-5 (low)" in text and "fix-date-range" in text and "6/6" in text
    assert "Projected cost of one side of a day" in text and "4 trials $" in text


def test_tasks_are_ordered_by_tier_then_id(tmp_path: Path) -> None:
    records = retier(
        baseline_records(tmp_path),
        {"add-top-flag": "hard", "fix-csv-total": "easy", "fix-date-range": "medium"},
        {"add-top-flag": 1, "fix-csv-total": 1, "fix-date-range": 1},
    )
    (config,) = CalibrationReport.from_records(records).configs
    assert [(t.difficulty, t.task_id) for t in config.tasks] == [
        ("easy", "fix-csv-total"),
        ("medium", "fix-date-range"),
        ("hard", "add-top-flag"),
    ]


def test_tasks_above_easy_are_flagged_outside_the_band(tmp_path: Path) -> None:
    records = retier(
        baseline_records(tmp_path, trials=4),
        TIERS,
        {"add-top-flag": 0, "fix-csv-total": 2, "fix-date-range": 4},
    )
    # add-top-flag is easy at 0%: never flagged. fix-csv-total medium at 50%: in the band.
    # fix-date-range hard at 100%: too easy.
    report = CalibrationReport.from_records(records)
    (config,) = report.configs
    assert [t.task_id for t in report.out_of_band_tasks(config)] == ["fix-date-range"]
    text = report.render()
    assert "too easy (> 75%)" in text and "too hard" not in text


def test_a_hard_task_that_never_passes_is_too_hard(tmp_path: Path) -> None:
    records = retier(
        baseline_records(tmp_path, trials=4),
        TIERS,
        {"add-top-flag": 4, "fix-csv-total": 4, "fix-date-range": 0},
    )
    report = CalibrationReport.from_records(records)
    flagged = [t.task_id for t in report.out_of_band_tasks(report.configs[0])]
    assert flagged == ["fix-csv-total", "fix-date-range"]  # the medium one is too easy
    text = report.render()
    assert "too hard (< 30%)" in text and "too easy (> 75%)" in text


@pytest.mark.parametrize(
    ("passes", "verdict"),
    [
        ({"add-top-flag": 4, "fix-csv-total": 2, "fix-date-range": 2}, "inside"),
        ({"add-top-flag": 4, "fix-csv-total": 4, "fix-date-range": 4}, "OUTSIDE"),
        ({"add-top-flag": 0, "fix-csv-total": 0, "fix-date-range": 0}, "OUTSIDE"),
    ],
)
def test_the_overall_rate_is_checked_against_the_target(
    tmp_path: Path, passes: dict[str, int], verdict: str
) -> None:
    records = retier(baseline_records(tmp_path, trials=4), TIERS, passes)
    assert f"is {verdict} the target 60%-75%" in CalibrationReport.from_records(records).render()


def test_overall_and_tier_totals_are_listed(tmp_path: Path) -> None:
    records = retier(
        baseline_records(tmp_path, trials=4),
        TIERS,
        {"add-top-flag": 4, "fix-csv-total": 2, "fix-date-range": 1},
    )
    (config,) = CalibrationReport.from_records(records).configs
    assert config.totals() == (7, 12)
    assert config.totals("easy") == (4, 4) and config.totals("hard") == (1, 4)
    text = CalibrationReport.from_records(records).render()
    assert "7/12" in text and "medium" in text


def test_errors_are_not_counted_as_attempts(tmp_path: Path) -> None:
    records = baseline_records(tmp_path, trials=2)
    broken = [records[0].model_copy(update={"status": "error", "passed": False}), *records[1:]]
    (config,) = CalibrationReport.from_records(broken).configs
    stats = {t.task_id: t for t in config.tasks}[records[0].task_id]
    assert (stats.trials, stats.errors) == (1, 1)
    assert "1 error(s) not counted" in CalibrationReport.from_records(broken).render()


def test_two_configs_in_one_directory_are_shown_side_by_side(tmp_path: Path) -> None:
    store_dir = tmp_path / "shared"
    first = baseline_records(store_dir, trials=2)
    second = baseline_records(store_dir, model="claude-sonnet-5-5", trials=1, date="2026-11-03")
    assert len(second) == len(first) + 3
    report = CalibrationReport.from_records(second)
    assert [c.model for c in report.configs] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    text = report.render()
    assert "claude-haiku-5-5 (low)" in text and "claude-sonnet-5-5 (low)" in text
    assert "claude-haiku-5-5 (low) + claude-sonnet-5-5 (low): 4 trials $" in text
    haiku, sonnet = report.configs
    assert sonnet.cost_of_one_side(2) == pytest.approx(2 * sonnet.cost_of_one_side(1))
    assert sonnet.cost_of_one_side(1) > haiku.cost_of_one_side(1)  # same tokens, dearer model


def test_the_bands_are_constructor_arguments(tmp_path: Path) -> None:
    records = retier(
        baseline_records(tmp_path, trials=4),
        TIERS,
        {"add-top-flag": 4, "fix-csv-total": 2, "fix-date-range": 2},
    )
    strict = CalibrationReport.from_records(records, task_band=(0.6, 0.9), overall_band=(0.9, 1.0))
    assert [t.task_id for t in strict.out_of_band_tasks(strict.configs[0])] == [
        "fix-csv-total",
        "fix-date-range",
    ]
    assert "OUTSIDE the target 90%-100%" in strict.render()


def test_without_records_there_is_nothing_to_show() -> None:
    assert CalibrationReport.from_records([]).render() == "no attempts recorded"


def test_failures_are_listed_with_label_source_and_note(tmp_path: Path) -> None:
    records = baseline_records(tmp_path, trials=2)

    def fail(record: AttemptRecord, label: str | None) -> AttemptRecord:
        update: dict[str, object] = {"passed": False, "triage_cost_usd": 0.0004}
        if label:
            update |= {"triage_label": label, "triage_source": "model", "triage_note": "why"}
        return record.model_copy(update=update)

    records = [
        fail(records[0], "incomplete_fix"),
        fail(records[1], "incomplete_fix"),
        fail(records[2], None),
        *records[3:],
    ]
    text = CalibrationReport.from_records(records).render()
    assert "failures: incomplete_fix 2, unlabelled 1" in text
    assert "triage cost $0.0012 (not in the attempt costs above)" in text
    assert "incomplete_fix [model] why" in text and "unlabelled" in text


def test_a_config_without_failures_prints_no_failure_lines(tmp_path: Path) -> None:
    text = CalibrationReport.from_records(baseline_records(tmp_path, trials=2)).render()
    assert "failures:" not in text and "triage cost" not in text


def test_a_long_failure_list_is_capped(tmp_path: Path) -> None:
    records = [
        r.model_copy(update={"passed": False}) for r in baseline_records(tmp_path, trials=10)
    ]
    text = CalibrationReport.from_records(records).render()
    assert "... and 10 more" in text
