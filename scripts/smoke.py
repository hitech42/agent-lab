"""Manual smoke test: REAL attempts against the Anthropic API. Never run in CI.

Runs one attempt per tool set (bash_only, then structured) in throwaway temp dirs.

Usage: ANTHROPIC_API_KEY=... uv run python scripts/smoke.py
"""

import os
import sys
import tempfile
from pathlib import Path

import anthropic
from _common import format_trace

from agent_lab.harness.agent import Agent
from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig, ToolSet
from agent_lab.harness.model_client import ModelClient, RetryPolicy
from agent_lab.harness.pricing import PriceTable
from agent_lab.harness.redact import Redactor
from agent_lab.harness.sandbox import Sandbox

MODEL = "claude-haiku-5-5"
PROMPTS: dict[ToolSet, str] = {
    "bash_only": "Create hello.py that prints hello, then run it.",
    "structured": (
        "Create hello.py that prints hello and test_hello.py that checks its output, "
        "then run the tests."
    ),
}


def run_one(tool_set: ToolSet, client: ModelClient, prices: PriceTable, redactor: Redactor) -> bool:
    config = AgentConfig(
        name=f"smoke-{tool_set}",
        model=MODEL,
        system_prompt="You are a coding agent. Use the tools to complete the task, then stop.",
        tool_set=tool_set,
        max_turns=8,
        max_tokens_per_call=2000,
        tool_output_max_chars=4000,
    )
    # An unknown model fails here, before any API call.
    budget = Budget(max_cost_usd=0.25, max_total_tokens=100_000, price=prices.price(config.model))
    agent = Agent.from_config(config, client, redactor)

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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1
    # The harness owns retries, so disable the SDK's own.
    client = ModelClient(anthropic.Anthropic(max_retries=0), RetryPolicy())
    prices = PriceTable.from_yaml(PriceTable.default_path())
    redactor = Redactor.from_env()
    results = [run_one(tool_set, client, prices, redactor) for tool_set in PROMPTS]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
