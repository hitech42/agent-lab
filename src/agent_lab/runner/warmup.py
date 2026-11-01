"""Warm the prompt cache once per distinct config before the day's attempts."""

from pydantic import BaseModel

from agent_lab.harness.agent import Agent
from agent_lab.harness.model_client import ModelClient
from agent_lab.harness.pricing import PriceTable


class AbortCause(BaseModel):
    """Why a run had to stop (an exception's class, HTTP status and message)."""

    reason: str
    error_type: str | None = None
    error_status: int | None = None
    message: str | None = None


class WarmupResult(BaseModel):
    cost_usd: float = 0.0
    calls: int = 0
    failure: AbortCause | None = None


class CacheWarmer:
    """One minimal call per distinct config with the attempt's exact cached prefix.

    It doubles as the run's canary: any failure (after RetryPolicy's retries) is
    reported so the runner can abort before spending anything on attempts.
    """

    def __init__(self, client: ModelClient, prices: PriceTable) -> None:
        self._client = client
        self._prices = prices

    def warm(self, agents: dict[str, Agent]) -> WarmupResult:
        """Warm every agent (keyed by config hash). Stops at the first failure."""
        result = WarmupResult()
        for agent in agents.values():
            request = agent.warmup_request()
            try:
                response = self._client.create(request)
            except Exception as exc:
                result.failure = AbortCause(
                    reason="warmup_failed",
                    error_type=type(exc).__name__,
                    error_status=getattr(exc, "status_code", None),
                    message=str(exc),
                )
                return result
            result.calls += 1
            result.cost_usd += self._prices.cost(request.model, response.usage)
        return result
