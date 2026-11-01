"""The experiment schedule: baseline config, defaults and one track per ISO weekday."""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_lab.harness.config import AgentConfig
from agent_lab.harness.context import ContextStrategy
from agent_lab.harness.pricing import PriceTable, PricingError


class ScheduleError(Exception):
    """The schedule is invalid. ``problems`` lists every problem found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid schedule:\n" + "\n".join(f"  - {p}" for p in problems))


class _UniqueKeyLoader(yaml.SafeLoader):
    """A YAML loader that rejects duplicate mapping keys instead of keeping the last."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class Track(BaseModel):
    """One weekday's experiment as written in schedule.yaml (data only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    question: str = Field(min_length=1)
    variant: dict[str, Any] = Field(default_factory=dict)
    trials_per_task: int | None = Field(default=None, gt=0)
    daily_max_cost_usd: float | None = Field(default=None, gt=0)
    attempt_max_cost_usd: float | None = Field(default=None, gt=0)


class ScheduleDefaults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trials_per_task: int = Field(gt=0)
    daily_max_cost_usd: float = Field(gt=0)
    attempt_max_cost_usd: float = Field(gt=0)


class ScheduleFile(BaseModel):
    """The raw shape of schedule.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline: dict[str, Any]
    defaults: ScheduleDefaults
    cost_defaults: dict[str, float]
    tracks: dict[int, Track]


class ResolvedTrack(BaseModel):
    """A track with both configs fully resolved (prompt text inlined) and its caps."""

    model_config = ConfigDict(frozen=True)

    weekday: int
    track: Track
    baseline: AgentConfig
    variant: AgentConfig
    trials_per_task: int
    daily_max_cost_usd: float
    attempt_max_cost_usd: float


class ExperimentSchedule:
    """Validated schedule with every weekday's track resolved."""

    def __init__(
        self,
        baseline: AgentConfig,
        tracks: dict[int, ResolvedTrack],
        cost_defaults: dict[str, float],
    ) -> None:
        self.baseline = baseline
        self.cost_defaults = dict(cost_defaults)  # per-attempt estimate by model, USD
        self._tracks = dict(tracks)

    @classmethod
    def from_yaml(cls, path: Path, prices: PriceTable) -> ExperimentSchedule:
        """Load and validate; raises ScheduleError listing every problem."""
        try:
            raw = yaml.load(path.read_text(), Loader=_UniqueKeyLoader)
            parsed = ScheduleFile.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise ScheduleError([f"{path}: {cls._describe(exc)}"]) from exc
        problems: list[str] = []
        prompts_dir = path.parent / "prompts"
        baseline = cls._resolve(parsed.baseline, prompts_dir, prices, "baseline", problems)
        if sorted(parsed.tracks) != list(range(1, 8)):
            problems.append(
                "tracks must have exactly one entry per ISO weekday 1-7, "
                f"got {sorted(parsed.tracks)}"
            )
        tracks: dict[int, ResolvedTrack] = {}
        for weekday, track in sorted(parsed.tracks.items()):
            resolved = cls._resolve_track(weekday, track, parsed, prompts_dir, prices, problems)
            if resolved is not None:
                tracks[weekday] = resolved
        problems += cls._consistency_problems(parsed, tracks)
        if problems or baseline is None:
            raise ScheduleError(problems)
        return cls(baseline, tracks, parsed.cost_defaults)

    def track_for(self, day: date) -> ResolvedTrack:
        return self._tracks[day.isoweekday()]

    def track_named(self, name: str) -> ResolvedTrack:
        for weekday in sorted(self._tracks):
            if self._tracks[weekday].track.name == name:
                return self._tracks[weekday]
        known = ", ".join(sorted({t.track.name for t in self._tracks.values()}))
        raise ScheduleError([f"unknown track {name!r} (known: {known})"])

    @staticmethod
    def config_hash(config: AgentConfig) -> str:
        """First 12 hex of sha256 over the canonical JSON of the config, minus its name.

        The prompt text is part of the config, so editing a prompt changes the hash;
        renaming a config does not.
        """
        canonical = json.dumps(
            config.model_dump(exclude={"name"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    @classmethod
    def _resolve_track(
        cls,
        weekday: int,
        track: Track,
        parsed: ScheduleFile,
        prompts_dir: Path,
        prices: PriceTable,
        problems: list[str],
    ) -> ResolvedTrack | None:
        label = f"track {weekday} ({track.name})"
        if "name" in track.variant:
            problems.append(f"{label}: 'name' cannot be overridden")
            return None
        fields = {**parsed.baseline, **track.variant, "name": track.name}
        baseline = cls._resolve(parsed.baseline, prompts_dir, prices, label + " baseline", problems)
        variant = cls._resolve(fields, prompts_dir, prices, label, problems)
        if baseline is None or variant is None:
            return None
        defaults = parsed.defaults
        for config in (baseline, variant):
            if config.model not in parsed.cost_defaults:
                problems.append(f"{label}: cost_defaults has no entry for model {config.model!r}")
        return ResolvedTrack(
            weekday=weekday,
            track=track,
            baseline=baseline,
            variant=variant,
            trials_per_task=track.trials_per_task or defaults.trials_per_task,
            daily_max_cost_usd=track.daily_max_cost_usd or defaults.daily_max_cost_usd,
            attempt_max_cost_usd=track.attempt_max_cost_usd or defaults.attempt_max_cost_usd,
        )

    @classmethod
    def _resolve(
        cls,
        fields: dict[str, Any],
        prompts_dir: Path,
        prices: PriceTable,
        label: str,
        problems: list[str],
    ) -> AgentConfig | None:
        """Turn raw fields (with a prompt file name) into a validated AgentConfig."""
        found: list[str] = []
        prompt_name = fields.get("system_prompt")
        text = ""
        if not isinstance(prompt_name, str):
            found.append("system_prompt must be a prompt file name")
        elif not (prompts_dir / prompt_name).is_file():
            found.append(f"prompt file {prompts_dir / prompt_name} does not exist")
        else:
            text = (prompts_dir / prompt_name).read_text().strip()
        config: AgentConfig | None = None
        try:
            config = AgentConfig.model_validate({**fields, "system_prompt": text})
        except ValidationError as exc:
            found.append(cls._describe(exc))
        if config is not None:
            found += cls._registered_problems(config, prices)
        problems += [f"{label}: {message}" for message in found]
        return config if not found else None

    @staticmethod
    def _registered_problems(config: AgentConfig, prices: PriceTable) -> list[str]:
        problems: list[str] = []
        try:
            prices.price(config.model)
        except PricingError:
            problems.append(f"model {config.model!r} has no entry in config/pricing.yaml")
        try:
            ContextStrategy.from_name(config.context_strategy)
        except NotImplementedError as exc:
            problems.append(str(exc))
        return problems

    @staticmethod
    def _consistency_problems(parsed: ScheduleFile, tracks: dict[int, ResolvedTrack]) -> list[str]:
        by_name: dict[str, Track] = {}
        problems: list[str] = []
        for weekday, track in sorted(parsed.tracks.items()):
            first = by_name.setdefault(track.name, track)
            if first != track:
                problems.append(
                    f"track {weekday}: name {track.name!r} is reused with a different definition"
                )
        return problems

    @staticmethod
    def _describe(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'value'}: {e['msg']}" for e in exc.errors()
            )
        return str(exc)
