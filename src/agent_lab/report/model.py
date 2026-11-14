"""Data models of the report. ``ReportData`` is exactly what summary.json serializes."""

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Exclusions(Frozen):
    """Pairs left out of a comparison (infrastructure, not agent behaviour)."""

    error: int = 0  # either side's attempt ended in status "error"
    tampered: int = 0  # environment_tampered on either side
    unpaired: int = 0  # one side never ran (budget stop, abort)

    @property
    def total(self) -> int:
        return self.error + self.tampered + self.unpaired


class SideStats(Frozen):
    n: int
    passed: int
    rate: float
    ci_low: float | None  # None: fewer included pairs than the minimum
    ci_high: float | None
    mean_cost_usd: float
    cost_per_solved_usd: float | None  # None when nothing was solved


class Delta(Frozen):
    """variant - baseline, as a mean with an interval (or just the mean if too few pairs)."""

    mean: float
    low: float | None
    high: float | None
    verdict: str


class DifficultyStats(Frozen):
    """Pass counts of one difficulty tier within a series (included pairs only)."""

    difficulty: str  # easy | medium | hard | unknown (records from before tiers existed)
    tasks: int
    pairs: int
    baseline_passed: int
    variant_passed: int
    baseline_rate: float
    variant_rate: float


class InjectionSideStats(Frozen):
    """One side's injection attempts: exposed, resisted among the exposed, exposure unknown.

    ``exposed`` counts attempts whose conversation showed the model a planted marker. Records
    written before exposure was tracked are ``unknown``; their old ``injection_resisted`` count
    is kept apart (``resisted_unknown``) and never mixed into ``resisted_rate``.
    """

    exposed: int
    resisted_exposed: int
    resisted_rate: float | None  # resisted_exposed / exposed; None when nothing was exposed
    unknown: int
    resisted_unknown: int


class InjectionStats(Frozen):
    """Exposure and resistance to the planted instructions, over injection-task pairs."""

    pairs: int
    baseline: InjectionSideStats
    variant: InjectionSideStats


class FailureSideStats(Frozen):
    """Failure labels of one side's failed attempts (included pairs only).

    ``labels`` counts each label; ``unlabelled`` counts failures without one (written before
    triage existed); ``model_labelled`` is how many labels came from the model (advisory).
    """

    failed: int
    labels: dict[str, int]
    model_labelled: int
    unlabelled: int


class FailureStats(Frozen):
    baseline: FailureSideStats
    variant: FailureSideStats


class SeriesReport(Frozen):
    """One (track, baseline config, variant config) series."""

    track: str
    question: str
    baseline_hash: str
    variant_hash: str
    suite_hash: str | None = None  # None: records from before suite versioning
    suite_tasks: int = 0  # distinct tasks in the series' records, excluded pairs included
    current: bool
    first_date: str
    last_date: str
    days: int
    pairs: int
    tasks: int
    excluded: Exclusions
    baseline: SideStats
    variant: SideStats
    delta_pass_pp: Delta
    delta_cost_usd: Delta
    delta_turns: Delta
    delta_output_tokens: Delta
    by_difficulty: list[DifficultyStats] = Field(default_factory=list)
    injection: InjectionStats | None = None  # None: no injection-task pairs
    failures: FailureStats | None = None  # None: no failed attempt on either side
    ceiling: bool
    noise_floor: bool
    method: str
    description: str  # what the variant changes vs the baseline


class TrackReport(Frozen):
    track: str
    current: SeriesReport
    older: list[SeriesReport]
    unattributed_exclusions: int = 0


class DayCost(Frozen):
    date: str
    track: str
    spend_usd: float


class ReportData(Frozen):
    latest_date: str | None
    days_run: int
    attempts: int
    spend_usd: float  # attempts and warm-ups
    spend_attempts_usd: float
    spend_warmups_usd: float
    spend_triage_usd: float = 0.0  # failure-triage model calls (not in attempts or warm-ups)
    unreadable: int  # unreadable result lines/files ignored
    min_pairs: int
    resamples: int
    cluster_min_tasks: int
    comparisons_shown: int
    comparisons_by_metric: dict[str, int]
    latest_day: str | None
    tracks: list[TrackReport]
    daily: list[DayCost]
