"""Series per suite, results by difficulty tier, and injection resistance."""

import re
import shutil
from pathlib import Path

import pytest
from report_support import MON, NEXT_TUE, SUN, TUE, edit_records, run_day, store
from runner_support import TASKS_DIR, make_plan

from agent_lab.report.analysis import ResultsAnalyzer
from agent_lab.report.model import ReportData

HASH = re.compile(r"[0-9a-f]{12}")
SECOND_TUE = "2026-11-17"


def analyze(results: Path, **kwargs: int) -> ReportData:
    kwargs.setdefault("resamples", 200)
    return ResultsAnalyzer(**kwargs).analyze(store(results))


def track(data: ReportData, name: str):  # type: ignore[no-untyped-def]
    return next(t for t in data.tracks if t.track == name)


def run_with(tmp: Path, results: Path, day: str, tasks_dir: Path, **options: object) -> None:
    plan = make_plan(day, tasks_dir, trials=2, **options)  # type: ignore[arg-type]
    run_day(tmp, results, day, plan=plan, tasks_dir=tasks_dir)


@pytest.fixture
def tasks(tmp_path: Path) -> Path:
    copy = tmp_path / "tasks"
    shutil.copytree(TASKS_DIR, copy)
    return copy


# --- suites ---------------------------------------------------------------------------------


def test_a_series_knows_its_suite_hash_and_task_count(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path / "a", results, TUE, tasks)
    entry = track(analyze(results), "tool-design")
    assert entry.current.suite_hash and HASH.fullmatch(entry.current.suite_hash)
    assert entry.current.suite_tasks == 3 and entry.older == []


def test_changing_a_task_starts_a_new_series_and_keeps_the_old_one(
    tmp_path: Path, tasks: Path
) -> None:
    results = tmp_path / "results"
    run_with(tmp_path / "a", results, TUE, tasks)
    run_with(tmp_path / "b", results, NEXT_TUE, tasks)  # same suite: the same series
    (tasks / "fix-date-range" / "project" / "calc.py").write_text("def add(a, b):\n    return 0\n")
    run_with(tmp_path / "c", results, SECOND_TUE, tasks)
    entry = track(analyze(results), "tool-design")
    assert entry.current.last_date == SECOND_TUE and entry.current.pairs == 6
    (old,) = entry.older
    assert old.last_date == NEXT_TUE and old.first_date == TUE and old.pairs == 12
    assert old.suite_hash != entry.current.suite_hash
    assert (old.baseline_hash, old.variant_hash) == (
        entry.current.baseline_hash,
        entry.current.variant_hash,
    )


def test_a_subset_run_is_its_own_series(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path / "a", results, TUE, tasks)
    run_with(tmp_path / "b", results, NEXT_TUE, tasks, tasks=["fix-date-range"])
    entry = track(analyze(results), "tool-design")
    assert entry.current.suite_tasks == 1 and len(entry.older) == 1


def test_records_from_before_versioning_form_their_own_series(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path / "a", results, TUE, tasks)
    edit_records(
        results,
        lambda r: r.model_copy(
            update={"suite_hash": None, "task_hash": None, "task_difficulty": None}
        ),
    )
    run_with(tmp_path / "b", results, NEXT_TUE, tasks)
    entry = track(analyze(results), "tool-design")
    assert entry.current.suite_hash is not None and entry.current.last_date == NEXT_TUE
    (old,) = entry.older
    assert old.suite_hash is None and old.pairs == 6 and old.suite_tasks == 3
    assert [d.difficulty for d in old.by_difficulty] == ["unknown"]


def test_a_pair_whose_sides_disagree_on_the_suite_is_excluded(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path, results, TUE, tasks)

    def split(record):  # type: ignore[no-untyped-def]
        if record.config_role == "variant" and record.task_id == "fix-date-range":
            return record.model_copy(update={"suite_hash": "f" * 12})
        return record

    edit_records(results, split)
    series = track(analyze(results), "tool-design").current
    assert series.excluded.unpaired == 2 and series.pairs == 4  # both trials of that task


def test_the_suite_hash_is_in_summary_json_fields(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path, results, TUE, tasks)
    dumped = analyze(results).model_dump(mode="json")
    current = dumped["tracks"][0]["current"]
    assert HASH.fullmatch(current["suite_hash"]) and current["suite_tasks"] == 3
    assert current["by_difficulty"] and "injection" in current


# --- difficulty -------------------------------------------------------------------------------


def set_tiers(results: Path, tiers: dict[str, str]) -> None:
    edit_records(results, lambda r: r.model_copy(update={"task_difficulty": tiers[r.task_id]}))


def test_pass_rates_by_difficulty_per_side(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path, results, TUE, tasks)
    set_tiers(
        results,
        {"add-top-flag": "easy", "fix-csv-total": "medium", "fix-date-range": "hard"},
    )

    def outcomes(record):  # type: ignore[no-untyped-def]
        hard_baseline_fails = (
            record.task_id == "fix-date-range" and record.config_role == "baseline"
        )
        medium_variant_fails = record.task_id == "fix-csv-total" and record.config_role == "variant"
        failed = hard_baseline_fails or medium_variant_fails
        return record.model_copy(update={"passed": not failed})

    edit_records(results, outcomes)
    series = track(analyze(results), "tool-design").current
    tiers = {d.difficulty: d for d in series.by_difficulty}
    assert [d.difficulty for d in series.by_difficulty] == ["easy", "medium", "hard"]
    assert (tiers["easy"].pairs, tiers["easy"].tasks) == (2, 1)
    assert (tiers["easy"].baseline_rate, tiers["easy"].variant_rate) == (1.0, 1.0)
    assert (tiers["medium"].baseline_passed, tiers["medium"].variant_passed) == (2, 0)
    assert (tiers["hard"].baseline_rate, tiers["hard"].variant_rate) == (0.0, 1.0)


def test_excluded_pairs_do_not_count_in_a_tier(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path, results, TUE, tasks)

    def break_one(record):  # type: ignore[no-untyped-def]
        if (
            record.task_id == "fix-date-range"
            and record.trial == 1
            and record.config_role == "variant"
        ):
            return record.model_copy(update={"status": "error", "passed": False})
        return record

    edit_records(results, break_one)
    series = track(analyze(results), "tool-design").current
    assert series.excluded.error == 1
    assert sum(d.pairs for d in series.by_difficulty) == series.pairs == 5


# --- injection --------------------------------------------------------------------------------


def test_injection_resistance_per_side_over_injection_pairs(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path, results, TUE, tasks)  # fix-csv-total is the injection task, 2 trials

    def tempted(record):  # type: ignore[no-untyped-def]
        if (
            record.task_category == "injection"
            and record.config_role == "baseline"
            and record.trial == 1
        ):
            return record.model_copy(update={"injection_resisted": False})
        return record

    edit_records(results, tempted)
    injection = track(analyze(results), "tool-design").current.injection
    assert injection is not None and injection.pairs == 2
    assert (injection.baseline_resisted, injection.variant_resisted) == (1, 2)
    assert (injection.baseline_rate, injection.variant_rate) == (0.5, 1.0)


def test_no_injection_stats_without_injection_pairs(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path, results, SUN, tasks, tasks=["fix-date-range"])
    assert track(analyze(results), "aa-test").current.injection is None


def test_an_unknown_resistance_is_not_counted_as_resisted(tmp_path: Path, tasks: Path) -> None:
    results = tmp_path / "results"
    run_with(tmp_path, results, MON, tasks, tasks=["fix-csv-total"])
    edit_records(results, lambda r: r.model_copy(update={"injection_resisted": None}))
    injection = track(analyze(results), "model-comparison").current.injection
    assert injection is not None and (injection.baseline_resisted, injection.variant_resisted) == (
        0,
        0,
    )
