"""Pydantic models describing an agent configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolSet = Literal["structured", "bash_only"]
ContextStrategyName = Literal["full", "truncate_tool_outputs", "summarize"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentConfig(BaseModel):
    """Everything that defines one agent variant. Immutable once built."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system_prompt: str
    effort: Effort  # sent as output_config.effort on every request; no default on purpose
    tool_set: ToolSet
    max_turns: int = Field(gt=0)
    max_tokens_per_call: int = Field(gt=0)
    tool_output_max_chars: int = Field(gt=0)
    context_strategy: ContextStrategyName = "full"
