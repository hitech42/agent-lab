"""scripts/calibrate.py is manual (it calls the real API), but its plumbing is tested here."""

import importlib.util
import subprocess
import sys
from pathlib import Path

from agent_lab.runner.storage import ResultsStore

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calibrate.py"


def load_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("calibrate_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_defaults_are_four_trials_all_tasks_and_a_two_dollar_stop() -> None:
    args = load_script().parse_args([])
    assert (args.trials, args.tasks, args.model, args.results_dir) == (4, None, None, None)
    assert args.max_cost == 2.0 and args.max_attempt_cost is None and args.isolation == "none"


def test_the_options_are_parsed() -> None:
    args = load_script().parse_args(
        ["--tasks", "a,b", "--trials", "2", "--model", "claude-sonnet-5-5", "--max-cost", "1.5"]
    )
    assert (args.tasks, args.trials, args.model, args.max_cost) == (
        "a,b",
        2,
        "claude-sonnet-5-5",
        1.5,
    )


def test_each_run_into_one_directory_uses_the_next_free_day(tmp_path: Path) -> None:
    script = load_script()
    store = ResultsStore(tmp_path)
    first = script.next_free_day(store)
    store.attempts_path(first).parent.mkdir(parents=True)
    store.attempts_path(first).write_text("")
    second = script.next_free_day(store)
    assert second > first
    store.summary_path(second).write_text("{}")
    assert script.next_free_day(store) > second


def test_help_runs_without_a_key_or_any_network() -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 0 and "--trials" in done.stdout and "--model" in done.stdout
