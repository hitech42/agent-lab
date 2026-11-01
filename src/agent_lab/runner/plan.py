"""Plan one day's attempts: which task, which config, in what order."""

import random
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent_lab.benchmark.loader import TaskSuite
from agent_lab.runner.schedule import ExperimentSchedule, ResolvedTrack

ConfigRole = Literal["baseline", "variant"]


class PlannedAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempt_id: str
    task_id: str
    config_role: ConfigRole
    trial: int
    order: int


class PlanOptions(BaseModel):
    """Command-line overrides of the plan (all optional)."""

    model_config = ConfigDict(frozen=True)

    track: str | None = None
    trials: int | None = None
    tasks: list[str] | None = None


class RunPlan(BaseModel):
    """The ordered attempts for one date, with the resolved track they belong to."""

    model_config = ConfigDict(frozen=True)

    date: str
    resolved: ResolvedTrack
    attempts: list[PlannedAttempt]


class RunPlanner:
    """Interleaves baseline and variant: both run back to back for every (trial, task).

    The order within each pair comes from ``random.Random(<date string>)``, so the same
    date, schedule and options always give the same plan.
    """

    def __init__(self, schedule: ExperimentSchedule, suite: TaskSuite) -> None:
        self._schedule = schedule
        self._suite = suite

    def plan(self, day: date, options: PlanOptions) -> RunPlan:
        resolved = self._resolve(day, options)
        task_ids = options.tasks or [task.id for task in self._suite.all()]
        for task_id in task_ids:
            self._suite.get(task_id)  # raises UnknownTaskError for an unknown id
        rng = random.Random(day.isoformat())
        attempts: list[PlannedAttempt] = []
        for trial in range(1, resolved.trials_per_task + 1):
            for task_id in task_ids:
                roles: list[ConfigRole] = ["baseline", "variant"]
                rng.shuffle(roles)
                for role in roles:
                    attempts.append(
                        PlannedAttempt(
                            attempt_id=f"{day.isoformat()}-{task_id}-{role}-t{trial}",
                            task_id=task_id,
                            config_role=role,
                            trial=trial,
                            order=len(attempts) + 1,
                        )
                    )
        return RunPlan(date=day.isoformat(), resolved=resolved, attempts=attempts)

    def _resolve(self, day: date, options: PlanOptions) -> ResolvedTrack:
        resolved = (
            self._schedule.track_named(options.track)
            if options.track
            else self._schedule.track_for(day)
        )
        if options.trials is not None:
            resolved = resolved.model_copy(update={"trials_per_task": options.trials})
        return resolved
