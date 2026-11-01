from typing import Any, ClassVar

import pytest

from agent_lab.harness.config import AgentConfig
from agent_lab.harness.pricing import PricingError
from agent_lab.runner import wiring
from agent_lab.runner.wiring import AgentFactory, MissingApiKeyError


class RecordingAnthropic:
    instances: ClassVar[list[RecordingAnthropic]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.messages = None
        type(self).instances.append(self)


def config(model: str = "claude-haiku-5-5") -> AgentConfig:
    return AgentConfig(
        name="c",
        model=model,
        system_prompt="p",
        tool_set="structured",
        max_turns=3,
        max_tokens_per_call=100,
        tool_output_max_chars=100,
    )


def test_a_missing_or_empty_key_is_a_clear_error() -> None:
    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY is not set"):
        AgentFactory.from_env({})
    with pytest.raises(MissingApiKeyError):
        AgentFactory.from_env({"ANTHROPIC_API_KEY": ""})


def test_the_real_client_is_built_from_the_environment_key_with_sdk_retries_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingAnthropic.instances.clear()
    monkeypatch.setattr(wiring.anthropic, "Anthropic", RecordingAnthropic)
    factory = AgentFactory.from_env({"ANTHROPIC_API_KEY": "sk-ant-test-key-1"})
    (instance,) = RecordingAnthropic.instances
    assert instance.kwargs == {"api_key": "sk-ant-test-key-1", "max_retries": 0}
    assert factory.redactor.redact("leak sk-ant-test-key-1") == "leak [REDACTED]"


def test_agents_and_budgets_share_the_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring.anthropic, "Anthropic", RecordingAnthropic)
    factory = AgentFactory.from_env({"ANTHROPIC_API_KEY": "k"})
    assert factory.agent(config()) is not factory.agent(config())  # a fresh agent per call
    budget = factory.budget(config(), max_cost_usd=0.15)
    assert budget.max_cost_usd == 0.15 and budget.price == factory.prices.price("claude-haiku-5-5")


def test_an_unknown_model_fails_when_the_budget_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring.anthropic, "Anthropic", RecordingAnthropic)
    factory = AgentFactory.from_env({"ANTHROPIC_API_KEY": "k"})
    with pytest.raises(PricingError, match="no pricing for model 'gpt-9'"):
        factory.budget(config("gpt-9"), max_cost_usd=1.0)
