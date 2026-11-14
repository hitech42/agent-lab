from typing import get_args

import pytest
from pydantic import ValidationError

from agent_lab.harness.config import AgentConfig, Effort


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


def test_cache_messages_is_off_by_default_and_accepts_true_and_false(config: AgentConfig) -> None:
    assert config.cache_messages is None
    for value in (True, False):
        assert (
            AgentConfig(**{**config.model_dump(), "cache_messages": value}).cache_messages is value
        )
    with pytest.raises(ValidationError):
        AgentConfig(**{**config.model_dump(), "cache_messages": "sometimes"})


def build(**changes: object) -> AgentConfig:
    base: dict[str, object] = {
        "name": "n",
        "model": "m",
        "system_prompt": "s",
        "effort": "medium",
        "tool_set": "bash_only",
        "max_turns": 1,
        "max_tokens_per_call": 1,
        "tool_output_max_chars": 1,
    }
    return AgentConfig(**{**base, **changes})  # type: ignore[arg-type]


def test_context_parameters_default_to_none() -> None:
    cfg = build()
    assert (cfg.context_keep_last, cfg.context_after_turns, cfg.context_summary_max_tokens) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"context_strategy": "truncate_tool_outputs", "context_keep_last": 2},
        {
            "context_strategy": "summarize",
            "context_keep_last": 2,
            "context_after_turns": 4,
            "context_summary_max_tokens": 300,
        },
    ],
)
def test_valid_strategy_parameters(fields: dict[str, object]) -> None:
    assert build(**fields).context_strategy == fields["context_strategy"]


@pytest.mark.parametrize(
    "fields",
    [
        {"context_keep_last": 2},  # full takes none
        {"context_strategy": "truncate_tool_outputs"},  # keep_last missing
        {"context_strategy": "truncate_tool_outputs", "context_keep_last": 0},
        {
            "context_strategy": "truncate_tool_outputs",
            "context_keep_last": 2,
            "context_after_turns": 4,
        },
        {"context_strategy": "summarize", "context_keep_last": 2},
        {
            "context_strategy": "summarize",
            "context_keep_last": 4,
            "context_after_turns": 4,
            "context_summary_max_tokens": 300,
        },
        {
            "context_strategy": "summarize",
            "context_keep_last": 2,
            "context_after_turns": 4,
            "context_summary_max_tokens": 0,
        },
    ],
)
def test_invalid_strategy_parameters(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        build(**fields)


def test_effort_is_required_and_limited_to_the_five_levels(config: AgentConfig) -> None:
    data = config.model_dump()
    del data["effort"]
    with pytest.raises(ValidationError, match="effort"):
        AgentConfig(**data)
    for level in get_args(Effort):
        assert AgentConfig(**{**data, "effort": level}).effort == level
    for bad in ("none", "MEDIUM", ""):
        with pytest.raises(ValidationError, match="effort"):
            AgentConfig(**{**data, "effort": bad})
    assert get_args(Effort) == ("low", "medium", "high", "xhigh", "max")
