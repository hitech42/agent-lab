"""A tiny in-memory scheduler for recurring jobs.

Times are whole seconds on a clock the caller provides; the scheduler never reads the
real clock.
"""

import re
from collections.abc import Sequence

_INTERVAL = re.compile(r"^(\d+)([smh])$")
_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_interval(text: str) -> dict[str, int | str]:
    """Parse an interval such as ``"90s"``, ``"5m"`` or ``"2h"``.

    Returns a new dict with the keys ``unit`` (``"s"``, ``"m"`` or ``"h"``) and
    ``seconds``. The caller owns the result and may modify it: that never affects
    the result of another call. Raises ``ValueError`` for anything else, including a
    zero interval.
    """
    match = _INTERVAL.match(text.strip())
    if match is None or int(match.group(1)) == 0:
        raise ValueError(f"bad interval {text!r}")
    unit = match.group(2)
    return {"unit": unit, "seconds": int(match.group(1)) * _SECONDS[unit]}


class Job:
    """A recurring job.

    ``tags`` are labels. The tags of a new job are the tags it was given, in order,
    followed by its own interval tag (``"every-m"`` for ``"5m"``). The list that was
    passed in is never modified, and no two jobs share a tags list.

    ``next_run`` is when the job is next due. A new job is due immediately
    (``next_run == 0``); ``mark_run(now)`` makes it due again ``interval`` seconds after
    ``now``. Each job has its own schedule, even when jobs share an interval.
    """

    def __init__(
        self, name: str, interval: str, priority: int = 0, tags: Sequence[str] = ()
    ) -> None:
        self.name = name
        self.spec = parse_interval(interval)
        self.priority = priority
        self.tags = [*tags, f"every-{self.spec['unit']}"]

    @property
    def next_run(self) -> int:
        return int(self.spec.get("next_run", 0))

    def mark_run(self, now: int) -> None:
        """Record that the job ran at ``now``."""
        self.spec["next_run"] = now + int(self.spec["seconds"])


class Scheduler:
    """Keeps jobs by name. Every scheduler has its own jobs."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def add(self, name: str, interval: str, priority: int = 0) -> Job:
        """Add a job; ``ValueError`` if this scheduler already has one with that name."""
        if name in self.jobs:
            raise ValueError(f"duplicate job {name!r}")
        job = Job(name, interval, priority)
        self.jobs[name] = job
        return job

    def names(self) -> list[str]:
        """The names of this scheduler's jobs, in the order they were added."""
        return list(self.jobs)

    def due(self, now: int) -> list[Job]:
        """The jobs that are due at ``now`` (``next_run <= now``).

        Highest priority first; jobs with the same priority are in the order they were
        added. This does not change the scheduler or its jobs: calling it twice with the
        same ``now`` gives the same answer.
        """
        ready = [job for job in self.jobs.values() if job.next_run <= now]
        return sorted(ready, key=lambda job: -job.priority)

    def run_due(self, now: int) -> list[str]:
        """Mark every due job as run at ``now``; return their names in the order of ``due``."""
        names = []
        for job in self.due(now):
            job.mark_run(now)
            names.append(job.name)
        return names
