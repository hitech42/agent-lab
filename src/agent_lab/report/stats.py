"""Statistics for the report: pure functions over plain numbers, standard library only.

- Wilson score interval for a proportion.
- Newcombe's hybrid score interval (method 10, Newcombe 1998, Statistics in Medicine 17:2635)
  for the difference of two paired proportions. Unlike a bootstrap it does not collapse to
  [0, 0] when there are no discordant pairs.
- Percentile bootstrap of a mean, by pair or by cluster (task), seeded from the data itself so
  the same input always gives the same interval.
"""

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist

_Z = NormalDist().inv_cdf(0.975)  # 1.959964: two-sided 95%

INSUFFICIENT = "insufficient data"
NO_DIFFERENCE = "no detectable difference"


@dataclass(frozen=True)
class Interval:
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


def mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values) if values else 0.0


def wilson_interval(successes: int, n: int) -> Interval:
    """Wilson 95% score interval for ``successes`` out of ``n`` (whole range when n == 0)."""
    if n <= 0:
        return Interval(0.0, 1.0)
    p = successes / n
    z2 = _Z * _Z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = _Z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return Interval(max(0.0, centre - half), min(1.0, centre + half))


def newcombe_paired_difference(
    only_variant: int, only_baseline: int, both: int, neither: int
) -> Interval:
    """Newcombe's hybrid score interval (method 10) for variant - baseline pass rate.

    The four counts are the cells of the paired 2x2 table; the interval is built from the
    Wilson intervals of the two marginal rates and the phi correlation of the table.
    """
    n = only_variant + only_baseline + both + neither
    if n <= 0:
        return Interval(-1.0, 1.0)
    variant_total = both + only_variant
    baseline_total = both + only_baseline
    p1, p2 = variant_total / n, baseline_total / n
    w1, w2 = wilson_interval(variant_total, n), wilson_interval(baseline_total, n)
    l1, u1, l2, u2 = w1.low, w1.high, w2.low, w2.high
    margins = variant_total * (only_baseline + neither) * baseline_total * (only_variant + neither)
    phi = 0.0
    if margins > 0:
        # Newcombe's continuity correction: a positive association is shrunk by n/2 (and
        # taken as 0 when it is smaller than that); a negative one is used as it is.
        cross: float = both * neither - only_variant * only_baseline
        if cross > n / 2:
            cross -= n / 2
        elif cross >= 0:
            cross = 0
        phi = cross / math.sqrt(margins)
    delta = p1 - p2
    low = delta - math.sqrt(
        max(0.0, (p1 - l1) ** 2 + (u2 - p2) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2))
    )
    high = delta + math.sqrt(
        max(0.0, (u1 - p1) ** 2 + (p2 - l2) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2))
    )
    return Interval(max(-1.0, low), min(1.0, high))


def bootstrap_mean_intervals(
    columns: Mapping[str, Sequence[float]],
    clusters: Sequence[str] | None = None,
    resamples: int = 2000,
) -> dict[str, Interval]:
    """95% percentile bootstrap interval of the mean of every column.

    ``columns`` hold one value per pair (all the same length). Without ``clusters`` pairs are
    resampled; with ``clusters`` (one label per pair, e.g. the task) whole clusters are, and
    the mean is the ratio of sums. All columns share each resample. The seed is a hash of the
    input, so the result depends on the data only.
    """
    names = sorted(columns)
    n = len(columns[names[0]]) if names else 0
    if n == 0:
        return {name: Interval(0.0, 0.0) for name in names}
    rng = random.Random(_seed(columns, clusters))
    groups = _groups(clusters, n)
    sums = {
        name: [math.fsum(columns[name][i] for i in members) for members in groups] for name in names
    }
    sizes = [len(members) for members in groups]
    draws: dict[str, list[float]] = {name: [] for name in names}
    count = len(groups)
    for _ in range(resamples):
        picks = [int(rng.random() * count) for _ in range(count)]
        total = sum(sizes[i] for i in picks)
        for name in names:
            draws[name].append(math.fsum(sums[name][i] for i in picks) / total)
    return {name: _percentile_interval(values) for name, values in draws.items()}


def verdict(estimate: float, interval: Interval | None) -> str:
    """Neutral wording: ``variant higher`` / ``variant lower`` / no detectable difference."""
    if interval is None:
        return INSUFFICIENT
    if interval.contains(0.0):
        return NO_DIFFERENCE
    return "variant higher" if estimate > 0 else "variant lower"


def at_ceiling(baseline_rate: float, variant_rate: float, threshold: float = 0.95) -> bool:
    """Both sides pass at least ``threshold``: a pass-rate comparison cannot show much."""
    return baseline_rate >= threshold and variant_rate >= threshold


def cost_per_solved(total_cost: float, passes: int) -> float | None:
    """Total cost divided by passes; None when nothing was solved."""
    return total_cost / passes if passes > 0 else None


def _groups(clusters: Sequence[str] | None, n: int) -> list[list[int]]:
    if clusters is None:
        return [[i] for i in range(n)]
    by_label: dict[str, list[int]] = {}
    for index, label in enumerate(clusters):
        by_label.setdefault(label, []).append(index)
    return [by_label[label] for label in sorted(by_label)]


def _percentile_interval(values: list[float]) -> Interval:
    ordered = sorted(values)
    top = len(ordered) - 1
    return Interval(ordered[round(0.025 * top)], ordered[round(0.975 * top)])


def _seed(columns: Mapping[str, Sequence[float]], clusters: Sequence[str] | None) -> int:
    canonical = json.dumps(
        {"columns": {k: list(v) for k, v in columns.items()}, "clusters": clusters},
        sort_keys=True,
        separators=(",", ":"),
    )
    return int.from_bytes(hashlib.sha256(canonical.encode()).digest()[:8], "big")
