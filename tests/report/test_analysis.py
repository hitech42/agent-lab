import json
from pathlib import Path

import pytest
from report_support import (
    MON,
    NEXT_TUE,
    SUN,
    TUE,
    WED,
    changed_variant,
    copy_results,
    realistic,
    run_day,
    store,
    warmup_fails,
)
from runner_support import (
    FakeGrader,
    default_responder,
    is_warmup,
    make_plan,
    stopped_response,
)

from agent_lab.report.analysis import ResultsAnalyzer
from agent_lab.report.model import ReportData


def analyze(results: Path, **kwargs: int) -> ReportData:
    kwargs.setdefault("resamples", 200)
    return ResultsAnalyzer(**kwargs).analyze(store(results))


def track(data: ReportData, name: str):  # type: ignore[no-untyped-def]
    return next(t for t in data.tracks if t.track == name)


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return realistic(tmp_path_factory.mktemp("realistic"))


# --- the realistic multi-day fixture --------------------------------------------------


def test_the_overall_numbers(results: Path) -> None:
    data = analyze(results)
    assert data.latest_date == NEXT_TUE and data.days_run == 5  # the abort-only day counts
    assert data.attempts == sum(
        len(r.read_text().splitlines()) for r in (results / "runs").glob("*.jsonl")
    )
    assert data.spend_usd == pytest.approx(data.spend_attempts_usd + data.spend_warmups_usd)
    assert data.spend_warmups_usd > 0 and data.unreadable == 0
    assert [d.date for d in data.daily] == [MON, TUE, WED, SUN, NEXT_TUE]
    assert {d.date: d.track for d in data.daily}[WED] == "context-truncate"


def test_every_track_has_a_current_series(results: Path) -> None:
    data = analyze(results)
    assert [t.track for t in data.tracks] == ["aa-test", "model-comparison", "tool-design"]
    assert all(t.current.current for t in data.tracks)  # context-truncate (abort only) has no pairs


def test_an_errored_attempt_excludes_its_pair(results: Path) -> None:
    series = track(analyze(results), "model-comparison").current
    assert series.pairs == 11 and series.excluded.error == 1 and series.excluded.total == 1


def test_aa_test_is_the_noise_floor_with_identical_hashes_and_a_ceiling(results: Path) -> None:
    series = track(analyze(results), "aa-test").current
    assert series.noise_floor and series.baseline_hash == series.variant_hash
    assert series.description == "identical configs" and series.ceiling
    assert series.delta_pass_pp.verdict == "no detectable difference"
    assert series.delta_pass_pp.low < 0 < series.delta_pass_pp.high  # Newcombe, not [0, 0]


def test_a_config_change_starts_a_new_series_and_the_old_one_is_kept(results: Path) -> None:
    tool = track(analyze(results), "tool-design")
    assert tool.current.last_date == NEXT_TUE and tool.current.pairs == 12
    assert len(tool.older) == 1 and tool.older[0].last_date == TUE and not tool.older[0].current
    assert tool.current.baseline_hash == tool.older[0].baseline_hash
    assert tool.current.variant_hash != tool.older[0].variant_hash
    assert "max_turns: 15 → 9" in tool.current.description
    assert "tool_set: structured → bash_only" in tool.older[0].description


def test_the_descriptions_come_from_the_configs_registry(results: Path) -> None:
    assert (
        "model: claude-haiku-5-5 → claude-sonnet-5-5"
        in track(analyze(results), "model-comparison").current.description
    )


def test_comparisons_shown_are_counted_per_metric(results: Path) -> None:
    data = analyze(results)
    series = sum(1 + len(t.older) for t in data.tracks)
    assert data.comparisons_by_metric == {"cost": series, "pass": series, "turns": series}
    assert data.comparisons_shown == 3 * series


def test_the_latest_day_outcome_comes_from_the_commit_message_builder(results: Path) -> None:
    assert analyze(results).latest_day.startswith("eval(tool-design): baseline 12/12")


def test_the_method_names_the_intervals_used(results: Path) -> None:
    method = track(analyze(results), "aa-test").current.method
    assert "Newcombe" in method and "bootstrap" in method


# --- exclusions -----------------------------------------------------------------------


def only_series(data: ReportData):  # type: ignore[no-untyped-def]
    assert len(data.tracks) == 1
    return data.tracks[0].current


def test_a_tampered_pair_is_excluded(tmp_path: Path) -> None:
    run_day(tmp_path / "w", tmp_path / "r", MON, grader=FakeGrader(tamper_from_call=2))
    series = only_series(analyze(tmp_path / "r"))
    assert series.excluded.tampered == 1 and series.pairs == 0
    assert series.delta_pass_pp.verdict == "insufficient data"


def test_a_pair_with_a_missing_side_is_excluded_as_unpaired(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "w", results, MON)
    path = results / "runs" / f"{MON}.jsonl"
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(lines[1:]))  # the first attempt of the first pair is gone
    series = only_series(analyze(results))
    assert series.pairs == 11 and series.excluded.unpaired == 1


def test_a_run_where_a_role_never_ran_is_attributed_to_the_matching_series(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "a", results, MON)
    run_day(tmp_path / "b", results, "2026-11-09")  # another Monday: the same series
    path = results / "runs" / "2026-11-09.jsonl"
    keep = [
        ln for ln in path.read_text().splitlines(keepends=True) if '"config_role":"baseline"' in ln
    ]
    path.write_text("".join(keep))  # no variant record at all in that run
    series = only_series(analyze(results))
    assert series.excluded.unpaired == 12 and series.pairs == 12


def test_a_run_with_no_match_is_counted_as_unattributed(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "a", results, MON)
    path = results / "runs" / f"{MON}.jsonl"
    keep = [
        ln for ln in path.read_text().splitlines(keepends=True) if '"config_role":"baseline"' in ln
    ]
    path.write_text("".join(keep))
    data = analyze(results)
    assert data.tracks == [] and data.attempts == 12


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal", "pause_turn"])
def test_agent_outcomes_stay_in_and_count_as_failures(tmp_path: Path, stop_reason: str) -> None:
    def responder(request):  # type: ignore[no-untyped-def]
        return default_responder(request) if is_warmup(request) else stopped_response(stop_reason)

    run_day(
        tmp_path / "w", tmp_path / "r", MON, responder=responder, grader=FakeGrader(passed=False)
    )
    series = only_series(analyze(tmp_path / "r"))
    assert series.pairs == 12 and series.excluded.total == 0
    assert series.baseline.passed == 0 and series.variant.passed == 0
    assert series.baseline.cost_per_solved_usd is None  # nothing solved


def test_a_truncated_attempt_that_still_passed_counts_as_a_pass(tmp_path: Path) -> None:
    def responder(request):  # type: ignore[no-untyped-def]
        return default_responder(request) if is_warmup(request) else stopped_response("max_tokens")

    run_day(tmp_path / "w", tmp_path / "r", MON, responder=responder)  # the grader passes them
    series = only_series(analyze(tmp_path / "r"))
    assert series.baseline.passed == 12 and series.pairs == 12


# --- small samples and the method switch ----------------------------------------------


def test_fewer_than_ten_pairs_gives_point_estimates_and_insufficient_data(tmp_path: Path) -> None:
    run_day(tmp_path / "w", tmp_path / "r", MON, trials=3)  # 9 pairs
    series = only_series(analyze(tmp_path / "r"))
    assert series.pairs == 9
    for item in (
        series.delta_pass_pp,
        series.delta_cost_usd,
        series.delta_turns,
        series.delta_output_tokens,
    ):
        assert item.low is None and item.high is None and item.verdict == "insufficient data"
    assert series.baseline.ci_low is None and series.variant.ci_high is None
    assert series.baseline.rate == 1.0 and "insufficient data" in series.method
    assert analyze(tmp_path / "r").comparisons_shown == 0


def test_ten_pairs_is_enough(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "w", results, MON, trials=4)
    assert analyze(results, min_pairs=12).tracks[0].current.delta_pass_pp.low is not None
    assert analyze(results, min_pairs=13).tracks[0].current.delta_pass_pp.low is None


def test_the_clustered_bootstrap_is_used_from_the_task_threshold(tmp_path: Path) -> None:
    run_day(tmp_path / "w", tmp_path / "r", MON, trials=4)
    pairs = only_series(analyze(tmp_path / "r"))
    clustered = only_series(analyze(tmp_path / "r", cluster_min_tasks=3))
    assert "Newcombe" in pairs.method and "clustered" not in pairs.method
    assert "task-clustered" in clustered.method and "Newcombe" not in clustered.method
    # all pairs pass, so the clustered pass interval is the bootstrap's zero width one
    assert clustered.delta_pass_pp.low == clustered.delta_pass_pp.high == 0.0
    assert pairs.delta_pass_pp.low < 0 < pairs.delta_pass_pp.high


# --- spend ----------------------------------------------------------------------------


def test_a_finished_day_spends_what_its_summary_says(results: Path) -> None:
    summary = json.loads((results / "runs" / f"{MON}.run.json").read_text())
    assert {d.date: d.spend_usd for d in analyze(results).daily}[MON] == pytest.approx(
        summary["total_cost_usd"]
    )


def test_an_abort_only_day_spends_its_warmup(results: Path) -> None:
    abort = next((results / "runs").glob(f"{WED}.abort-*.json"))
    assert {d.date: d.spend_usd for d in analyze(results).daily}[WED] == pytest.approx(
        json.loads(abort.read_text())["total_cost_usd"]
    )


def test_a_killed_run_falls_back_to_the_sum_of_its_records(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "w", results, MON)
    (results / "runs" / f"{MON}.run.json").unlink()
    data = analyze(results)
    assert data.daily[0].spend_usd == pytest.approx(data.spend_attempts_usd)
    assert data.spend_warmups_usd == 0.0


def test_a_day_with_aborts_and_a_resume_counts_every_segment_once(tmp_path: Path) -> None:
    results = tmp_path / "r"
    plan = run_day(tmp_path / "a", results, MON, responder=warmup_fails)
    run_day(tmp_path / "b", results, MON, plan=plan)  # resumes: runs the whole day
    data = analyze(results)
    summary = json.loads((results / "runs" / f"{MON}.run.json").read_text())
    assert summary["abort_count"] == 1
    assert data.spend_usd == pytest.approx(summary["total_cost_usd"])


# --- determinism and robustness -------------------------------------------------------


def test_the_analysis_is_deterministic(results: Path) -> None:
    assert analyze(results) == analyze(results)


def test_the_input_order_does_not_matter(results: Path, tmp_path: Path) -> None:
    shuffled = copy_results(results, tmp_path / "shuffled")
    for path in (shuffled / "runs").glob("*.jsonl"):
        path.write_text("".join(reversed(path.read_text().splitlines(keepends=True))))
    assert analyze(shuffled) == analyze(results)


def test_unreadable_lines_are_counted(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "w", results, MON)
    with (results / "runs" / f"{MON}.jsonl").open("a") as handle:
        handle.write('{"cut off\n')
    (results / "configs" / "broken.json").write_text("{nope")
    data = analyze(results)
    assert data.unreadable == 2 and data.attempts == 24


def test_a_missing_registry_entry_is_described_without_failing(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "w", results, MON)
    for path in (results / "configs").glob("*.json"):
        path.unlink()
    assert only_series(analyze(results)).description == "config not in registry"


def test_an_empty_results_directory_has_no_data(tmp_path: Path) -> None:
    data = analyze(tmp_path / "nothing")
    assert data.latest_date is None and data.tracks == [] and data.daily == []
    assert data.days_run == 0 and data.latest_day is None


def test_spend_is_not_double_counted_when_variants_change(tmp_path: Path) -> None:
    results = tmp_path / "r"
    run_day(tmp_path / "a", results, MON)
    run_day(
        tmp_path / "b",
        results,
        "2026-11-09",
        plan=changed_variant(make_plan("2026-11-09", trials=4), max_turns=9),
    )
    data = analyze(results)
    assert len(data.daily) == 2 and data.attempts == 48
    assert len(data.tracks) == 1 and len(data.tracks[0].older) == 1
    assert make_plan  # the helper is shared with other tests


def test_a_context_strategy_and_its_parameters_are_named_in_the_description() -> None:
    from runner_support import schedule

    from agent_lab.harness.config import AgentConfig
    from agent_lab.report.analysis import ResultsAnalyzer
    from agent_lab.runner.records import ConfigRecord
    from agent_lab.runner.schedule import ExperimentSchedule

    def record(config: AgentConfig) -> ConfigRecord:
        fields = config.model_dump(exclude={"name"})
        return ConfigRecord(
            config_hash=ExperimentSchedule.config_hash(config), first_name=config.name, **fields
        )

    sched = schedule()
    text = ResultsAnalyzer._describe(
        record(sched.baseline), record(sched.track_named("context-truncate").variant)
    )
    assert "context_strategy: full → truncate_tool_outputs" in text
    assert "context_keep_last: None → 2" in text
