"""Manual smoke test: REAL attempts against the Anthropic API. Never run in CI.

Runs one attempt per tool set (bash_only, then structured) in throwaway temp dirs.

Usage: ANTHROPIC_API_KEY=... uv run python scripts/smoke.py
"""

import sys
import tempfile
from pathlib import Path

from _common import format_trace

from agent_lab.harness.config import ToolSet
from agent_lab.harness.sandbox import Sandbox
from agent_lab.runner.schedule import ExperimentSchedule
from agent_lab.runner.wiring import AgentFactory, MissingApiKeyError

SCHEDULE = Path(__file__).resolve().parent.parent / "experiments" / "schedule.yaml"
PROMPTS: dict[ToolSet, str] = {
    "bash_only": "Create hello.py that prints hello, then run it.",
    "structured": (
        "Create hello.py that prints hello and test_hello.py that checks its output, "
        "then run the tests."
    ),
}


def run_one(tool_set: ToolSet, factory: AgentFactory, schedule: ExperimentSchedule) -> bool:
    config = schedule.baseline.model_copy(
        update={
            "name": f"smoke-{tool_set}",
            "tool_set": tool_set,
            "max_turns": 8,
            "max_tokens_per_call": 2000,
        }
    )
    # An unknown model fails here, before any API call.
    budget = factory.budget(config, max_cost_usd=0.25)
    agent = factory.agent(config)

    with tempfile.TemporaryDirectory() as tmp, Sandbox(Path(tmp)) as sandbox:
        result = agent.run(PROMPTS[tool_set], sandbox, budget)
        print(f"=== {tool_set} ===")
        print(format_trace(result.trace))
        print(f"status={result.status} stop_reason={result.stop_reason} error={result.error}")
        print(f"final_text: {result.final_text}")
        print(f"files: {sandbox.list_files()}")
        cost = "n/a" if result.cost_usd is None else f"{result.cost_usd:.6f}"
        print(f"cost_usd: {cost}\n")
    return result.status == "completed"


def main() -> int:
    try:
        factory = AgentFactory.from_env()
    except MissingApiKeyError as exc:
        print(exc, file=sys.stderr)
        return 1
    schedule = ExperimentSchedule.from_yaml(SCHEDULE, factory.prices)
    results = [run_one(tool_set, factory, schedule) for tool_set in PROMPTS]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
