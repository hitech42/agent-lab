"""Manual: run ONE real attempt at a task and grade it. Never run in CI.

Usage:
    ANTHROPIC_API_KEY=... uv run python scripts/run_task.py --task fix-date-range \\
        [--tool-set structured|bash_only] [--model claude-haiku-5-5]
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

import anthropic
from _common import format_trace

from agent_lab.benchmark.environment import EnvironmentIntegrity
from agent_lab.benchmark.grader import Grader
from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.workspace import TaskWorkspace
from agent_lab.harness.agent import Agent
from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig, ToolSet
from agent_lab.harness.model_client import ModelClient, RetryPolicy
from agent_lab.harness.pricing import PriceTable
from agent_lab.harness.redact import Redactor
from agent_lab.harness.sandbox import Sandbox

TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"
SYSTEM_PROMPT = (
    "You are a coding agent working in a project directory. Use the tools to complete "
    "the task, then stop."
)
MAX_TURNS = 15


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one real attempt at a task and grade it.")
    parser.add_argument("--task", required=True, help="task id (a directory under tasks/)")
    parser.add_argument("--tool-set", choices=["structured", "bash_only"], default="structured")
    parser.add_argument("--model", default="claude-haiku-5-5")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1

    task = TaskSuite.load(TASKS_DIR).get(args.task)
    tool_set: ToolSet = args.tool_set
    prices = PriceTable.from_yaml(PriceTable.default_path())
    config = AgentConfig(
        name=f"run-task-{task.id}",
        model=args.model,
        system_prompt=SYSTEM_PROMPT,
        tool_set=tool_set,
        max_turns=MAX_TURNS,
        max_tokens_per_call=4000,
        tool_output_max_chars=4000,
    )
    # An unknown model fails here, before any API call.
    budget = Budget(max_cost_usd=0.50, max_total_tokens=300_000, price=prices.price(config.model))
    # The harness owns retries, so disable the SDK's own.
    client = ModelClient(anthropic.Anthropic(max_retries=0), RetryPolicy())
    agent = Agent.from_config(config, client, Redactor.from_env())

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
