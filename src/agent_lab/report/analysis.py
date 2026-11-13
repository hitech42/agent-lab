"""Turn the committed results into ``ReportData``: pairs, exclusions, series, statistics."""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import NamedTuple

from pydantic import ValidationError

from agent_lab.benchmark.exposure import InjectionTally
from agent_lab.report import stats
from agent_lab.report.model import (
    DayCost,
    Delta,
    DifficultyStats,
    Exclusions,
    InjectionSideStats,
    InjectionStats,
    ReportData,
    SeriesReport,
    SideStats,
    TrackReport,
)
from agent_lab.runner.publish import CommitMessageBuilder
from agent_lab.runner.records import AbortRecord, AttemptRecord, ConfigRecord, RunSummary
from agent_lab.runner.storage import ResultsStore

NOISE_FLOOR_TRACK = "aa-test"
_CONFIG_FIELDS = (
    "model",
    "effort",
    "tool_set",
    "max_turns",
    "max_tokens_per_call",
    "tool_output_max_chars",
    "context_strategy",
    "context_keep_last",
    "context_after_turns",
    "context_summary_max_tokens",
)
_DIFFICULTIES = ("easy", "medium", "hard", "unknown")


class SeriesKey(NamedTuple):
    """What makes records comparable: a config or suite change starts a new series."""

    track: str
    baseline_hash: str
    variant_hash: str
    suite_hash: str  # "" for records from before suite versioning


@dataclass(frozen=True)
class _Pair:
    date: str
    run_id: str
    task_id: str
    trial: int
    baseline: AttemptRecord
    variant: AttemptRecord


class ResultsAnalyzer:
    """Pairs, exclusions and series from ResultsStore.

    A pair is the baseline and variant record with the same (run_id, task_id, trial). A pair
    is excluded when either side errored or was tampered with, or when a side is missing;
    every other outcome (truncated, refused, incomplete, max_turns, budget_exceeded) is the
    agent's and counts as a failure unless it passed. Series group by (track, baseline hash,
    variant hash, suite hash), so editing a config or a task starts a new series.
    """

    def __init__(
        self, resamples: int = 2000, cluster_min_tasks: int = 8, min_pairs: int = 10
    ) -> None:
        self._resamples = resamples
        self._cluster_min_tasks = cluster_min_tasks
        self._min_pairs = min_pairs

    def analyze(self, store: ResultsStore) -> ReportData:
        records, bad_lines = store.read_all_attempts()
        summaries, bad_summaries = store.read_summaries()
        aborts, bad_aborts = store.read_aborts()
        configs, bad_configs = store.read_configs()
        by_hash = {c.config_hash: c for c in configs}
        included, exclusions, unattributed, suite_tasks = self._pair_up(records)
        series = [
            self._series(
                key, pairs, exclusions.get(key, Exclusions()), by_hash, len(suite_tasks[key])
            )
            for key, pairs in sorted(included.items())
        ]
        for key in sorted(set(exclusions) - set(included)):
            series.append(
                self._series(key, [], exclusions[key], by_hash, len(suite_tasks.get(key, ())))
            )
        tracks = self._tracks(series, unattributed)
        daily, attempts_spend = self._spend(records, summaries, aborts)
        total = math.fsum(d.spend_usd for d in daily)
        shown = Counter(
            {
                metric: sum(1 for s in series if s.pairs >= self._min_pairs)
                for metric in ("pass", "cost", "turns")
            }
        )
        dates = {r.date for r in records} | {s.date for s in summaries} | {a.date for a in aborts}
        return ReportData(
            latest_date=max(dates) if dates else None,
            days_run=len(dates),
            attempts=len(records),
            spend_usd=total,
            spend_attempts_usd=attempts_spend,
            spend_warmups_usd=max(0.0, total - attempts_spend),
            unreadable=bad_lines + bad_summaries + bad_aborts + bad_configs,
            min_pairs=self._min_pairs,
            resamples=self._resamples,
            cluster_min_tasks=self._cluster_min_tasks,
            comparisons_shown=sum(shown.values()),
            comparisons_by_metric=dict(sorted(shown.items())),
            latest_day=self._latest_day(store, dates),
            tracks=tracks,
            daily=daily,
        )

    # --- pairing ---------------------------------------------------------------------------

    def _pair_up(
        self, records: list[AttemptRecord]
    ) -> tuple[
        dict[SeriesKey, list[_Pair]],
        dict[SeriesKey, Exclusions],
        dict[str, int],
        dict[SeriesKey, set[str]],
    ]:
        run_hashes: dict[str, dict[str, str]] = defaultdict(dict)
        run_suites: dict[str, str] = {}
        for record in sorted(records, key=lambda r: r.attempt_id):
            run_hashes[record.run_id].setdefault(record.config_role, record.config_hash)
            run_suites.setdefault(record.run_id, record.suite_hash or "")
        grouped: dict[tuple[str, str, int], dict[str, AttemptRecord]] = defaultdict(dict)
        for record in records:
            grouped[(record.run_id, record.task_id, record.trial)][record.config_role] = record
        included: dict[SeriesKey, list[_Pair]] = defaultdict(list)
        counts: dict[SeriesKey, Counter[str]] = defaultdict(Counter)
        unattributed: Counter[str] = Counter()
        tasks_seen: dict[SeriesKey, set[str]] = defaultdict(set)
        for (run_id, task_id, trial), sides in sorted(grouped.items()):
            baseline, variant = sides.get("baseline"), sides.get("variant")
            track = (baseline or variant).track  # type: ignore[union-attr]
            if baseline is None or variant is None:
                key = self._key_for_unpaired(
                    track, run_hashes[run_id], run_suites[run_id], list(included) + list(counts)
                )
                if key is None:
                    unattributed[track] += 1
                else:
                    counts[key]["unpaired"] += 1
                continue
            key = SeriesKey(
                track, baseline.config_hash, variant.config_hash, baseline.suite_hash or ""
            )
            tasks_seen[key].add(task_id)
            if baseline.suite_hash != variant.suite_hash:  # a resume that mixed two suites
                counts[key]["unpaired"] += 1
            elif "error" in (baseline.status, variant.status):
                counts[key]["error"] += 1
            elif baseline.environment_tampered or variant.environment_tampered:
                counts[key]["tampered"] += 1
            else:
                included[key].append(
                    _Pair(baseline.date, run_id, task_id, trial, baseline, variant)
                )
        exclusions = {
            key: Exclusions(error=c["error"], tampered=c["tampered"], unpaired=c["unpaired"])
            for key, c in counts.items()
        }
        return dict(included), exclusions, dict(unattributed), dict(tasks_seen)

    @staticmethod
    def _key_for_unpaired(
        track: str, hashes: dict[str, str], suite: str, known: list[SeriesKey]
    ) -> SeriesKey | None:
        """The series an unpaired attempt belongs to: its run's hashes, else a matching series."""
        baseline, variant = hashes.get("baseline"), hashes.get("variant")
        if baseline and variant:
            return SeriesKey(track, baseline, variant, suite)
        matches = sorted(
            k
            for k in known
            if k.track == track
            and k.suite_hash == suite
            and (k.baseline_hash == baseline or k.variant_hash == variant)
        )
        return matches[-1] if matches else None

    # --- series ----------------------------------------------------------------------------

    def _series(
        self,
        key: SeriesKey,
        pairs: list[_Pair],
        excluded: Exclusions,
        configs: dict[str, ConfigRecord],
        suite_tasks: int,
    ) -> SeriesReport:
        track, baseline_hash, variant_hash, suite_hash = key
        pairs = sorted(pairs, key=lambda p: (p.date, p.run_id, p.task_id, p.trial))
        n = len(pairs)
        enough = n >= self._min_pairs
        tasks = len({p.task_id for p in pairs})
        clustered = tasks >= self._cluster_min_tasks
        b_pass = [1.0 if p.baseline.passed else 0.0 for p in pairs]
        v_pass = [1.0 if p.variant.passed else 0.0 for p in pairs]
        columns = {
            "pass": [v - b for v, b in zip(v_pass, b_pass, strict=True)],
            "cost": [p.variant.cost_usd - p.baseline.cost_usd for p in pairs],
            "turns": [float(p.variant.turns - p.baseline.turns) for p in pairs],
            "output": [float(p.variant.output_tokens - p.baseline.output_tokens) for p in pairs],
        }
        intervals: dict[str, stats.Interval] = {}
        if enough:
            intervals = stats.bootstrap_mean_intervals(
                columns, [p.task_id for p in pairs] if clustered else None, self._resamples
            )
            if not clustered:
                intervals["pass"] = self._newcombe(b_pass, v_pass)
        question = pairs[0].baseline.question if pairs else ""
        b_rate = sum(b_pass) / n if n else 0.0
        v_rate = sum(v_pass) / n if n else 0.0
        return SeriesReport(
            track=track,
            question=question,
            baseline_hash=baseline_hash,
            variant_hash=variant_hash,
            suite_hash=suite_hash or None,
            suite_tasks=suite_tasks,
            current=False,
            first_date=pairs[0].date if pairs else "",
            last_date=pairs[-1].date if pairs else "",
            days=len({p.date for p in pairs}),
            pairs=n,
            tasks=tasks,
            excluded=excluded,
            baseline=self._side([p.baseline for p in pairs], enough),
            variant=self._side([p.variant for p in pairs], enough),
            delta_pass_pp=self._delta(columns["pass"], intervals.get("pass"), 100.0),
            delta_cost_usd=self._delta(columns["cost"], intervals.get("cost")),
            delta_turns=self._delta(columns["turns"], intervals.get("turns")),
            delta_output_tokens=self._delta(columns["output"], intervals.get("output")),
            by_difficulty=self._by_difficulty(pairs),
            injection=self._injection(pairs),
            ceiling=bool(n) and stats.at_ceiling(b_rate, v_rate),
            noise_floor=track == NOISE_FLOOR_TRACK,
            method=self._method(enough, clustered),
            description=self._describe(configs.get(baseline_hash), configs.get(variant_hash)),
        )

    @staticmethod
    def _by_difficulty(pairs: list[_Pair]) -> list[DifficultyStats]:
        tiers: dict[str, list[_Pair]] = defaultdict(list)
        for pair in pairs:
            tiers[pair.baseline.task_difficulty or "unknown"].append(pair)
        return [
            DifficultyStats(
                difficulty=tier,
                tasks=len({p.task_id for p in tiers[tier]}),
                pairs=len(tiers[tier]),
                baseline_passed=sum(1 for p in tiers[tier] if p.baseline.passed),
                variant_passed=sum(1 for p in tiers[tier] if p.variant.passed),
                baseline_rate=sum(1 for p in tiers[tier] if p.baseline.passed) / len(tiers[tier]),
                variant_rate=sum(1 for p in tiers[tier] if p.variant.passed) / len(tiers[tier]),
            )
            for tier in _DIFFICULTIES
            if tiers.get(tier)
        ]

    @staticmethod
    def _injection(pairs: list[_Pair]) -> InjectionStats | None:
        injected = [p for p in pairs if p.baseline.task_category == "injection"]
        if not injected:
            return None
        return InjectionStats(
            pairs=len(injected),
            baseline=ResultsAnalyzer._injection_side([p.baseline for p in injected]),
            variant=ResultsAnalyzer._injection_side([p.variant for p in injected]),
        )

    @staticmethod
    def _injection_side(records: list[AttemptRecord]) -> InjectionSideStats:
        tally = InjectionTally()
        for record in records:
            tally.add(record.injection_exposed, record.injection_resisted)
        return InjectionSideStats(
            exposed=tally.exposed,
            resisted_exposed=tally.resisted_exposed,
            resisted_rate=tally.resisted_rate,
            unknown=tally.unknown,
            resisted_unknown=tally.resisted_unknown,
        )

    @staticmethod
    def _newcombe(baseline: list[float], variant: list[float]) -> stats.Interval:
        pairs = list(zip(baseline, variant, strict=True))
        return stats.newcombe_paired_difference(
            only_variant=sum(1 for b, v in pairs if v and not b),
            only_baseline=sum(1 for b, v in pairs if b and not v),
            both=sum(1 for b, v in pairs if b and v),
            neither=sum(1 for b, v in pairs if not b and not v),
        )

    @staticmethod
    def _method(enough: bool, clustered: bool) -> str:
        if not enough:
            return "insufficient data: point estimates only"
        if clustered:
            return "task-clustered percentile bootstrap for every metric, pass rate included"
        return (
            "pass rate: Newcombe hybrid score interval (paired, method 10); cost, turns, "
            "output tokens: pair percentile bootstrap"
        )

    def _side(self, records: list[AttemptRecord], enough: bool) -> SideStats:
        n = len(records)
        passed = sum(1 for r in records if r.passed)
        total_cost = math.fsum(r.cost_usd for r in records)
        interval = stats.wilson_interval(passed, n) if enough else None
        return SideStats(
            n=n,
            passed=passed,
            rate=passed / n if n else 0.0,
            ci_low=interval.low if interval else None,
            ci_high=interval.high if interval else None,
            mean_cost_usd=total_cost / n if n else 0.0,
            cost_per_solved_usd=stats.cost_per_solved(total_cost, passed),
        )

    @staticmethod
    def _delta(values: list[float], interval: stats.Interval | None, scale: float = 1.0) -> Delta:
        centre = stats.mean(values)
        return Delta(
            mean=centre * scale,
            low=interval.low * scale if interval else None,
            high=interval.high * scale if interval else None,
            verdict=stats.verdict(centre, interval),
        )

    @staticmethod
    def _describe(baseline: ConfigRecord | None, variant: ConfigRecord | None) -> str:
        """What the variant changes vs the baseline, from the configs registry."""
        if baseline is None or variant is None:
            return "config not in registry"
        changes = [
            f"{name}: {getattr(baseline, name)} → {getattr(variant, name)}"
            for name in _CONFIG_FIELDS
            if getattr(baseline, name) != getattr(variant, name)
        ]
        if baseline.system_prompt != variant.system_prompt:
            changes.append("system prompt changed")
        return "; ".join(changes) if changes else "identical configs"

    # --- tracks and spend ----------------------------------------------------------------------

    @staticmethod
    def _tracks(series: list[SeriesReport], unattributed: dict[str, int]) -> list[TrackReport]:
        by_track: dict[str, list[SeriesReport]] = defaultdict(list)
        for item in series:
            by_track[item.track].append(item)
        tracks: list[TrackReport] = []
        for name in sorted(by_track):
            ranked = sorted(
                by_track[name],
                key=lambda s: (
                    s.last_date,
                    s.pairs,
                    s.baseline_hash,
                    s.variant_hash,
                    s.suite_hash or "",
                ),
                reverse=True,
            )
            current = ranked[0].model_copy(update={"current": True})
            tracks.append(
                TrackReport(
                    track=name,
                    current=current,
                    older=ranked[1:],
                    unattributed_exclusions=unattributed.get(name, 0),
                )
            )
        return tracks

    @staticmethod
    def _spend(
        records: list[AttemptRecord], summaries: list[RunSummary], aborts: list[AbortRecord]
    ) -> tuple[list[DayCost], float]:
        """Spend per day: the summary's total, else the last abort's, else the records' sum."""
        by_day_records: dict[str, list[AttemptRecord]] = defaultdict(list)
        for record in records:
            by_day_records[record.date].append(record)
        summary_for = {s.date: s for s in summaries}
        aborts_for: dict[str, list[AbortRecord]] = defaultdict(list)
        for abort in aborts:
            aborts_for[abort.date].append(abort)
        days: list[DayCost] = []
        for day in sorted(set(by_day_records) | set(summary_for) | set(aborts_for)):
            if day in summary_for:
                spend, track = summary_for[day].total_cost_usd, summary_for[day].track
            elif day in aborts_for:
                last = max(aborts_for[day], key=lambda a: a.total_cost_usd)
                spend, track = last.total_cost_usd, last.track
            else:
                spend = math.fsum(r.cost_usd for r in by_day_records[day])
                track = by_day_records[day][0].track
            days.append(DayCost(date=day, track=track, spend_usd=spend))
        return days, math.fsum(r.cost_usd for r in records)

    @staticmethod
    def _latest_day(store: ResultsStore, dates: set[str]) -> str | None:
        if not dates:
            return None
        try:
            return CommitMessageBuilder().build(store.runs_dir.parent)
        except ValidationError, OSError, UnicodeDecodeError:
            return None
