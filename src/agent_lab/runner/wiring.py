"""Shared wiring of the model client, prices and redaction into agents and budgets."""

import os
from collections.abc import Mapping

import anthropic

from agent_lab.harness.agent import Agent
from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.model_client import ModelClient, RetryPolicy
from agent_lab.harness.pricing import PriceTable
from agent_lab.harness.redact import Redactor


class MissingApiKeyError(Exception):
    """ANTHROPIC_API_KEY is not set."""


class AgentFactory:
    """Builds agents and budgets that share one client, price table and redactor."""

    def __init__(self, client: ModelClient, prices: PriceTable, redactor: Redactor) -> None:
        self.client = client
        self.prices = prices
        self.redactor = redactor

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AgentFactory:
        """The real wiring. The API key comes from the environment only and is never shown."""
        env = os.environ if environ is None else environ
        key = env.get("ANTHROPIC_API_KEY")
        if not key:
            raise MissingApiKeyError("ANTHROPIC_API_KEY is not set")
        # The harness owns retries, so the SDK's own are disabled.
        raw = anthropic.Anthropic(api_key=key, max_retries=0)
        return cls(
            ModelClient(raw, RetryPolicy()),
            PriceTable.from_yaml(PriceTable.default_path()),
            Redactor.from_env(env),
        )

    def agent(self, config: AgentConfig) -> Agent:
        return Agent.from_config(config, self.client, self.redactor)

    def budget(self, config: AgentConfig, max_cost_usd: float) -> Budget:
        """A per-attempt budget; an unknown model fails here, before any API call."""
        return Budget(max_cost_usd=max_cost_usd, price=self.prices.price(config.model))
