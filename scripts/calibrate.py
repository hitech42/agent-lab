"""Manual: run the baseline config on some tasks and print per-task pass rates. Never in CI.

Usage:
    ANTHROPIC_API_KEY=... uv run python scripts/calibrate.py [--tasks id,id] [--trials 4] \\
        [--model claude-sonnet-5-5] [--results-dir DIR] [--max-cost 2.0]

Results go to a results directory that is NOT results/ (a fresh temporary directory by
default, printed at the start). Run the script twice with the same ``--results-dir`` (for
example once for the baseline and once with ``--model claude-sonnet-5-5 --trials 2``) and the
report shows both configs side by side. Each run uses the next calendar day that has no
results in that directory. A dry run first prints the plan and the cost estimate; the real run
stops at ``--max-cost``.
"""

import argparse
import os
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_lab.runner.calibration import CalibrationReport
from agent_lab.runner.run_command import RunArgs, RunCommand, RunDependencies
from agent_lab.runner.storage import ResultsStore

ROOT = Path(__file__).resolve().parent.parent


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate the baseline config on the tasks.")
    parser.add_argument("--tasks", help="comma-separated task ids (default: all)")
    parser.add_argument("--trials", type=int, default=4, help="attempts per task (default 4)")
    parser.add_argument("--model", help="run this model instead of the baseline's (e.g. Sonnet)")
    parser.add_argument("--results-dir", help="where to write (default: a new temporary dir)")
    parser.add_argument("--max-cost", type=float, default=2.0, help="hard stop in USD (default 2)")
    parser.add_argument(
        "--max-attempt-cost",
        type=float,
        help="per-attempt cap in USD (default: the schedule's, or 0.50 with --model)",
    )
    parser.add_argument("--isolation", choices=["none", "linux-user"], default="none")
    return parser.parse_args(argv)


def next_free_day(store: ResultsStore) -> str:
    day = datetime.now(UTC).date()
    while store.has_attempts(day.isoformat()) or store.has_summary(day.isoformat()):
        day += timedelta(days=1)
    return day.isoformat()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    results_dir = Path(args.results_dir or tempfile.mkdtemp(prefix="agent-lab-calibration-"))
    store = ResultsStore(results_dir)
    base = RunArgs(
        date=next_free_day(store),
        track="aa-test",  # identical variant: only the baseline side is run
        trials=args.trials,
        tasks=[t.strip() for t in args.tasks.split(",")] if args.tasks else None,
        results_dir=results_dir,
        transcripts_dir=results_dir / "transcripts",
        tasks_dir=ROOT / "tasks",
        schedule=ROOT / "experiments" / "schedule.yaml",
        isolation=args.isolation,
        baseline_only=True,
        model=args.model,
        max_cost_usd=args.max_cost,
        max_attempt_cost_usd=args.max_attempt_cost or (0.50 if args.model else None),
    )
    command = RunCommand(RunDependencies.default(os.environ, ROOT), sys.stdout)
    print(f"results directory: {results_dir}")
    if command.execute(replace(base, dry_run=True)) != 0:
        return 1
    print(f"\nrunning for real (stops at ${args.max_cost:.2f}) ...\n")
    code = command.execute(base)
    records, _ = store.read_all_attempts()
    print("\n" + CalibrationReport.from_records(records).render())
    print(f"inspect further: uv run agent-lab db rebuild --results-dir {results_dir}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
