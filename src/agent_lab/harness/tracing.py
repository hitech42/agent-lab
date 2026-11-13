"""Run tracing and trace record models."""

from pydantic import BaseModel, Field, computed_field


class TokenUsage(BaseModel):
    """Token counts for one API call (cache fields default to 0)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class ToolCallTrace(BaseModel):
    name: str
    duration_s: float
    is_error: bool


class TurnTrace(BaseModel):
    turn: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    stop_reason: str | None
    latency_s: float  # duration of the successful API call only
    total_latency_s: float  # whole turn API phase: failed calls, backoff sleeps, final call
    retries: int = 0
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class ContextCallTrace(BaseModel):
    """An extra model call made to build the context (e.g. a summary), not a turn.

    ``after_turn`` is the number of the model call whose request it was built for.
    """

    kind: str
    after_turn: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    stop_reason: str | None
    latency_s: float
    total_latency_s: float
    retries: int = 0


class AttemptTrace(BaseModel):
    """All turns of one attempt, with aggregate totals.

    Totals of tokens, latency and retries include ``context_calls`` (they were paid for and
    waited on); ``turns`` and the tool-call counts do not, so turn counts stay comparable.
    """

    turns: list[TurnTrace] = Field(default_factory=list)
    context_calls: list[ContextCallTrace] = Field(default_factory=list)

    def _all(self) -> list[TurnTrace | ContextCallTrace]:
        return [*self.turns, *self.context_calls]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self._all())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self._all())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cache_creation_input_tokens(self) -> int:
        return sum(t.cache_creation_input_tokens for t in self._all())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cache_read_input_tokens(self) -> int:
        return sum(t.cache_read_input_tokens for t in self._all())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_latency_s(self) -> float:
        return sum(t.total_latency_s for t in self._all())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_retries(self) -> int:
        return sum(t.retries for t in self._all())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tool_call_count(self) -> int:
        return sum(len(t.tool_calls) for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tool_error_count(self) -> int:
        return sum(c.is_error for t in self.turns for c in t.tool_calls)
