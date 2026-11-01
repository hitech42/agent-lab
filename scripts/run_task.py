"""Manual: run ONE real attempt at a task and grade it. Never run in CI.

Usage:
    ANTHROPIC_API_KEY=... uv run python scripts/run_task.py --task fix-date-range \\
        [--tool-set structured|bash_only] [--model claude-haiku-5-5]
"""

import argparse
import sys
import tempfile
from pathlib import Path

from _common import format_trace

from agent_lab.benchmark.environment import EnvironmentIntegrity
from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.config import ToolSet
from agent_lab.harness.sandbox import Sandbox
from agent_lab.runner.schedule import ExperimentSchedule
from agent_lab.runner.wiring import AgentFactory, MissingApiKeyError

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
SCHEDULE = ROOT / "experiments" / "schedule.yaml"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one real attempt at a task and grade it.")
    parser.add_argument("--task", required=True, help="task id (a directory under tasks/)")
    parser.add_argument("--tool-set", choices=["structured", "bash_only"], default="structured")
    parser.add_argument("--model", default="claude-haiku-5-5")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        factory = AgentFactory.from_env()
    except MissingApiKeyError as exc:
        print(exc, file=sys.stderr)
        return 1

    task = TaskSuite.load(TASKS_DIR).get(args.task)
    tool_set: ToolSet = args.tool_set
    schedule = ExperimentSchedule.from_yaml(SCHEDULE, factory.prices)
    config = schedule.baseline.model_copy(
        update={"name": f"run-task-{task.id}", "model": args.model, "tool_set": tool_set}
    )
    # An unknown model fails here, before any API call.
    budget = factory.budget(config, max_cost_usd=0.50)
    agent = factory.agent(config)

    integrity = EnvironmentIntegrity.for_running_interpreter()
    integrity.capture()
    grader = Grader.for_task(task, integrity)

    with (
        tempfile.TemporaryDirectory() as base,
        TaskWorkspace.create(task, Path(base)) as workspace,
        Sandbox(workspace.root) as sandbox,
    ):
        result = agent.run(task.prompt, sandbox, budget)
        grade = grader.grade(task, workspace, sandbox, result.final_text)

    print(format_trace(result.trace))
    print(f"status={result.status} stop_reason={result.stop_reason} error={result.error}")
    print(f"final_text: {result.final_text}")
    cost = "n/a" if result.cost_usd is None else f"{result.cost_usd:.6f}"
    print(f"cost_usd: {cost}")
    print(grade.model_dump_json(indent=2))
    return 0 if grade.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
