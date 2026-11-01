import math

import pytest

from agent_lab.report.stats import (
    INSUFFICIENT,
    NO_DIFFERENCE,
    Interval,
    at_ceiling,
    bootstrap_mean_intervals,
    cost_per_solved,
    newcombe_paired_difference,
    verdict,
    wilson_interval,
)

# --- Wilson ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("passed", "n", "low", "high"),
    [(0, 10, 0.0, 0.2775), (7, 10, 0.3968, 0.8922), (10, 10, 0.7225, 1.0), (12, 12, 0.7575, 1.0)],
)
def test_wilson_matches_reference_values(passed: int, n: int, low: float, high: float) -> None:
    interval = wilson_interval(passed, n)
    assert interval.low == pytest.approx(low, abs=5e-5) and interval.high == pytest.approx(
        high, abs=5e-5
    )


def test_wilson_stays_inside_zero_and_one_and_is_the_whole_range_without_data() -> None:
    assert wilson_interval(0, 0) == Interval(0.0, 1.0)
    for passed in range(0, 21):
        interval = wilson_interval(passed, 20)
        assert 0.0 <= interval.low <= passed / 20 <= interval.high <= 1.0


# --- Newcombe's hybrid score interval for paired proportions --------------------------


def test_newcombe_reproduces_the_published_worked_example() -> None:
    """n=50: both 20, first only 12, second only 2, neither 16 (StatsDirect's paired-proportions
    page, from Armitage and Berry): the difference 0.2 has the interval 0.056156 to 0.329207."""
    interval = newcombe_paired_difference(only_variant=12, only_baseline=2, both=20, neither=16)
    assert interval.low == pytest.approx(0.056156, abs=1e-6)
    assert interval.high == pytest.approx(0.329207, abs=1e-6)


def test_newcombe_is_not_degenerate_without_discordant_pairs() -> None:
    interval = newcombe_paired_difference(0, 0, both=12, neither=0)
    assert interval.low < 0 < interval.high  # a bootstrap would claim [0, 0]
    assert interval.low == pytest.approx(-0.2425, abs=1e-4)
    assert interval.high == pytest.approx(0.2425, abs=1e-4)
    same = newcombe_paired_difference(0, 0, both=0, neither=12)
    assert same.low < 0 < same.high


def test_newcombe_is_symmetric_when_the_sides_are_swapped() -> None:
    forward = newcombe_paired_difference(only_variant=5, only_baseline=1, both=4, neither=2)
    backward = newcombe_paired_difference(only_variant=1, only_baseline=5, both=4, neither=2)
    assert forward.low == pytest.approx(-backward.high) and forward.high == pytest.approx(
        -backward.low
    )


def test_newcombe_contains_the_observed_difference_and_stays_within_bounds() -> None:
    for a in range(0, 8):
        for b in range(0, 8):
            interval = newcombe_paired_difference(a, b, both=5, neither=3)
            delta = (a - b) / (a + b + 8)
            assert -1.0 <= interval.low <= delta <= interval.high <= 1.0


def test_newcombe_excludes_zero_for_a_clear_difference() -> None:
    interval = newcombe_paired_difference(only_variant=20, only_baseline=0, both=5, neither=5)
    assert interval.low > 0


def test_newcombe_without_pairs_is_uninformative() -> None:
    assert newcombe_paired_difference(0, 0, 0, 0) == Interval(-1.0, 1.0)


# --- bootstrap ------------------------------------------------------------------------

VALUES = [0.0, 1.0, 1.0, 0.0, 1.0, 2.0, -1.0, 0.5, 1.5, 0.0, 1.0, 2.0]


def test_bootstrap_is_deterministic_and_depends_only_on_the_data() -> None:
    first = bootstrap_mean_intervals({"x": VALUES}, resamples=300)
    assert first == bootstrap_mean_intervals({"x": list(VALUES)}, resamples=300)
    changed = bootstrap_mean_intervals({"x": [*VALUES[:-1], 2.5]}, resamples=300)
    assert changed != first


def test_bootstrap_interval_contains_the_mean_and_is_ordered() -> None:
    interval = bootstrap_mean_intervals({"x": VALUES}, resamples=500)["x"]
    centre = sum(VALUES) / len(VALUES)
    assert interval.low <= centre <= interval.high and interval.low < interval.high


def test_all_identical_values_give_a_zero_width_interval() -> None:
    interval = bootstrap_mean_intervals({"x": [0.25] * 9}, resamples=200)["x"]
    assert interval == Interval(0.25, 0.25)


def test_columns_are_independent_but_share_the_resamples() -> None:
    both = bootstrap_mean_intervals({"a": VALUES, "b": [v * 2 for v in VALUES]}, resamples=300)
    assert both["b"].low == pytest.approx(both["a"].low * 2) and both["b"].high == pytest.approx(
        both["a"].high * 2
    )


def test_the_interval_narrows_with_more_data() -> None:
    small = bootstrap_mean_intervals({"x": VALUES}, resamples=400)["x"]
    large = bootstrap_mean_intervals({"x": VALUES * 8}, resamples=400)["x"]
    assert (large.high - large.low) < (small.high - small.low)


def test_clustering_widens_the_interval_when_trials_of_a_task_agree() -> None:
    # Each of four tasks always gives the same delta, whatever the trial: strongly correlated.
    values = [1.0] * 6 + [0.0] * 6 + [1.0] * 6 + [-1.0] * 6
    clusters = [name for name in ("a", "b", "c", "d") for _ in range(6)]
    pairwise = bootstrap_mean_intervals({"x": values}, resamples=600)["x"]
    clustered = bootstrap_mean_intervals({"x": values}, clusters, resamples=600)["x"]
    assert (clustered.high - clustered.low) > (pairwise.high - pairwise.low)


def test_empty_input_has_a_degenerate_interval() -> None:
    assert bootstrap_mean_intervals({"x": []}) == {"x": Interval(0.0, 0.0)}
    assert bootstrap_mean_intervals({}) == {}


# --- verdict, ceiling, cost -----------------------------------------------------------


@pytest.mark.parametrize(
    ("estimate", "interval", "expected"),
    [
        (0.1, Interval(-0.2, 0.4), NO_DIFFERENCE),
        (0.0, Interval(0.0, 0.0), NO_DIFFERENCE),
        (0.3, Interval(0.1, 0.5), "variant higher"),
        (-0.3, Interval(-0.5, -0.1), "variant lower"),
        (0.0, Interval(0.0, 0.2), NO_DIFFERENCE),  # touching zero is not detectable
        (0.2, None, INSUFFICIENT),
    ],
)
def test_verdict_wording_is_neutral(
    estimate: float, interval: Interval | None, expected: str
) -> None:
    assert verdict(estimate, interval) == expected
    assert "better" not in expected and "worse" not in expected


def test_ceiling_needs_both_sides_at_95_percent() -> None:
    assert at_ceiling(0.95, 1.0) and at_ceiling(1.0, 1.0)
    assert not at_ceiling(0.94, 1.0) and not at_ceiling(1.0, 0.5)


def test_cost_per_solved_is_not_available_without_a_pass() -> None:
    assert cost_per_solved(0.12, 4) == pytest.approx(0.03)
    assert cost_per_solved(0.12, 0) is None
    assert not math.isnan(cost_per_solved(0.0, 3) or 0.0)
