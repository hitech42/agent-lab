"""Pydantic models describing an agent configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # Context strategy parameters (None = not used by the strategy; see _params_match_strategy).
    context_keep_last: int | None = Field(default=None, gt=0)
    context_after_turns: int | None = Field(default=None, gt=0)
    context_summary_max_tokens: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _params_match_strategy(self) -> AgentConfig:
        """Each strategy takes exactly its own parameters; the others must stay None."""
        given = {name for name in _PARAMS if getattr(self, name) is not None}
        required = _REQUIRED[self.context_strategy]
        if given != required:
            raise ValueError(
                f"context_strategy {self.context_strategy!r} takes exactly "
                f"{sorted(required) or 'no parameters'}, got {sorted(given) or 'none'}"
            )
        if (
            self.context_after_turns is not None
            and self.context_keep_last is not None
            and self.context_after_turns <= self.context_keep_last
        ):
            raise ValueError("context_after_turns must be greater than context_keep_last")
        return self


_PARAMS = ("context_keep_last", "context_after_turns", "context_summary_max_tokens")
_REQUIRED: dict[str, set[str]] = {
    "full": set(),
    "truncate_tool_outputs": {"context_keep_last"},
    "summarize": set(_PARAMS),
}
