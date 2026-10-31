import pytest
from pydantic import ValidationError

from agent_lab.harness.config import AgentConfig


def test_config_validation_and_frozen(config: AgentConfig) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(**{**config.model_dump(), "max_turns": 0})
    with pytest.raises(ValidationError):
        AgentConfig(**{**config.model_dump(), "tool_set": "other"})
    with pytest.raises(ValidationError):
        AgentConfig(**{**config.model_dump(), "unexpected": 1})
    with pytest.raises(ValidationError):
        config.max_turns = 5  # type: ignore[misc]
    assert config.context_strategy == "full"


def test_unimplemented_strategies_are_constructible_as_config() -> None:
    cfg = AgentConfig(
        name="n",
        model="m",
        system_prompt="s",
        tool_set="bash_only",
        max_turns=1,
        max_tokens_per_call=1,
        tool_output_max_chars=1,
        context_strategy="summarize",
    )
    assert cfg.context_strategy == "summarize"
