"""Calibration: what a config scores on the tasks, per task and per difficulty tier.

``CalibrationReport`` is built from the attempt records of a (temporary) results directory
that ``scripts/calibrate.py`` filled by running the baseline alone. Several configs in one
directory (for example the baseline and a Sonnet run) are shown side by side.
"""

from collections import defaultdict
from dataclasses import dataclass

from agent_lab.benchmark.exposure import InjectionTally
from agent_lab.runner.records import AttemptRecord

_TIERS = ("easy", "medium", "hard", "unknown")


@dataclass(frozen=True)
class TaskStats:
    """One task under one config. ``trials`` counts attempts that ran without ``error``."""

    task_id: str
    difficulty: str
    trials: int
    passed: int
    errors: int
    mean_turns: float
    mean_cost_usd: float

    @property
    def rate(self) -> float:
        return self.passed / self.trials if self.trials else 0.0


@dataclass(frozen=True)
class ConfigStats:
    config_hash: str
    model: str
    effort: str
    tasks: list[TaskStats]
    injection: InjectionTally | None = None  # None: no injection-task attempts

    @property
    def label(self) -> str:
        return f"{self.model} ({self.effort})"

    def totals(self, tier: str | None = None) -> tuple[int, int]:
        """(passed, trials) over all tasks, or over those of one tier."""
        chosen = [t for t in self.tasks if tier is None or t.difficulty == tier]
        return sum(t.passed for t in chosen), sum(t.trials for t in chosen)

    def cost_of_one_side(self, trials: int) -> float:
        """What running every task ``trials`` times costs on this config (attempts only)."""
        return sum(t.mean_cost_usd for t in self.tasks) * trials


class CalibrationReport:
    """Per-task pass rate, turns and cost for every config found in the records."""

    def __init__(
        self,
        configs: list[ConfigStats],
        task_band: tuple[float, float] = (0.30, 0.75),
        overall_band: tuple[float, float] = (0.60, 0.75),
        day_trials: tuple[int, ...] = (4, 2),
    ) -> None:
        self.configs = configs
        self._task_band = task_band
        self._overall_band = overall_band
        self._day_trials = day_trials

    @classmethod
    def from_records(cls, records: list[AttemptRecord], **options: object) -> CalibrationReport:
        by_config: dict[str, list[AttemptRecord]] = defaultdict(list)
        for record in sorted(records, key=lambda r: (r.date, r.attempt_id)):
            by_config[record.config_hash].append(record)
        configs = [cls._config(config_hash, rows) for config_hash, rows in by_config.items()]
        return cls(configs, **options)  # type: ignore[arg-type]

    @staticmethod
    def _config(config_hash: str, records: list[AttemptRecord]) -> ConfigStats:
        by_task: dict[str, list[AttemptRecord]] = defaultdict(list)
        for record in records:
            by_task[record.task_id].append(record)
        tasks = []
        for task_id, rows in by_task.items():
            ran = [r for r in rows if r.status != "error"]
            tasks.append(
                TaskStats(
                    task_id=task_id,
                    difficulty=rows[0].task_difficulty or "unknown",
                    trials=len(ran),
                    passed=sum(1 for r in ran if r.passed),
                    errors=len(rows) - len(ran),
                    mean_turns=sum(r.turns for r in ran) / len(ran) if ran else 0.0,
                    mean_cost_usd=sum(r.cost_usd for r in ran) / len(ran) if ran else 0.0,
                )
            )
        tasks.sort(key=lambda t: (_TIERS.index(t.difficulty), t.task_id))
        first = records[0]
        tally = InjectionTally()
        for record in records:
            if record.task_category == "injection" and record.status != "error":
                tally.add(record.injection_exposed, record.injection_resisted)
        return ConfigStats(
            config_hash,
            first.model,
            first.effort or "unknown",
            tasks,
            tally if tally.total else None,
        )

    def out_of_band_tasks(self, config: ConfigStats) -> list[TaskStats]:
        """Tasks above easy whose pass rate is outside the task band (the ones to adjust)."""
        low, high = self._task_band
        return [t for t in config.tasks if t.difficulty != "easy" and not low <= t.rate <= high]

    def render(self) -> str:
        if not self.configs:
            return "no attempts recorded"
        lines: list[str] = []
        for config in self.configs:
            lines += self._config_lines(config)
            lines.append("")
        lines += self._cost_lines()
        return "\n".join(lines).rstrip() + "\n"

    def _config_lines(self, config: ConfigStats) -> list[str]:
        low, high = self._task_band
        lines = [
            f"{config.label}  [{config.config_hash}]",
            f"  {'task':<26}{'tier':<8}{'pass':>9}{'turns':>8}{'cost':>10}  note",
        ]
        for t in config.tasks:
            note = ""
            if t.errors:
                note = f"{t.errors} error(s) not counted"
            if t.difficulty != "easy" and t.trials and t.rate < low:
                note = f"too hard (< {low:.0%})  {note}".strip()
            elif t.difficulty != "easy" and t.trials and t.rate > high:
                note = f"too easy (> {high:.0%})  {note}".strip()
            lines.append(
                f"  {t.task_id:<26}{t.difficulty:<8}{f'{t.passed}/{t.trials}':>6} {t.rate:>4.0%}"
                f"{t.mean_turns:>8.1f}{f'${t.mean_cost_usd:.4f}':>10}  {note}".rstrip()
            )
        passed, trials = config.totals()
        rate = passed / trials if trials else 0.0
        lines.append(f"  {'overall':<34}{f'{passed}/{trials}':>6} {rate:>4.0%}")
        for tier in _TIERS:
            tier_passed, tier_trials = config.totals(tier)
            if tier_trials:
                tier_rate = tier_passed / tier_trials
                lines.append(f"  {tier:<34}{f'{tier_passed}/{tier_trials}':>6} {tier_rate:>4.0%}")
        if config.injection is not None:
            lines.append(self._injection_line(config.injection))
        low_all, high_all = self._overall_band
        verdict = "inside" if low_all <= rate <= high_all else "OUTSIDE"
        lines.append(f"  overall {rate:.0%} is {verdict} the target {low_all:.0%}-{high_all:.0%}")
        return lines

    @staticmethod
    def _injection_line(t: InjectionTally) -> str:
        rate = "n/a" if t.resisted_rate is None else f"{t.resisted_rate:.0%}"
        line = (
            f"  injection: exposed {t.exposed}/{t.total}, "
            f"resisted {t.resisted_exposed}/{t.exposed} among exposed ({rate}), "
            f"exposure unknown {t.unknown}/{t.total}"
        )
        if t.unknown:
            line += f" (resisted {t.resisted_unknown} of those)"
        return line

    def _cost_lines(self) -> list[str]:
        lines = ["Projected cost of one side of a day (every task, attempts only):"]
        for config in self.configs:
            costs = ", ".join(
                f"{n} trials ${config.cost_of_one_side(n):.2f}" for n in self._day_trials
            )
            lines.append(f"  {config.label}: {costs}")
        if len(self.configs) > 1:
            base = self.configs[0]
            lines.append("A day pairing the first config with each other one:")
            for other in self.configs[1:]:
                costs = ", ".join(
                    f"{n} trials ${base.cost_of_one_side(n) + other.cost_of_one_side(n):.2f}"
                    for n in self._day_trials
                )
                lines.append(f"  {base.label} + {other.label}: {costs}")
        return lines
