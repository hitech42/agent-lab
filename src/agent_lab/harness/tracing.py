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
    latency_s: float
    retries: int = 0
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class AttemptTrace(BaseModel):
    """All turns of one attempt, with aggregate totals."""

    turns: list[TurnTrace] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cache_creation_input_tokens(self) -> int:
        return sum(t.cache_creation_input_tokens for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cache_read_input_tokens(self) -> int:
        return sum(t.cache_read_input_tokens for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_latency_s(self) -> float:
        return sum(t.latency_s for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_retries(self) -> int:
        return sum(t.retries for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tool_call_count(self) -> int:
        return sum(len(t.tool_calls) for t in self.turns)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tool_error_count(self) -> int:
        return sum(c.is_error for t in self.turns for c in t.tool_calls)

    def total_usage(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
            cache_creation_input_tokens=self.total_cache_creation_input_tokens,
            cache_read_input_tokens=self.total_cache_read_input_tokens,
        )

    def summary(self) -> str:
        lines = []
        for t in self.turns:
            calls = ", ".join(
                f"{c.name}{'!' if c.is_error else ''}({c.duration_s:.2f}s)" for c in t.tool_calls
            )
            lines.append(
                f"turn {t.turn}: in={t.input_tokens} out={t.output_tokens} "
                f"cache_w={t.cache_creation_input_tokens} cache_r={t.cache_read_input_tokens} "
                f"stop={t.stop_reason} {t.latency_s:.2f}s retries={t.retries}"
                + (f" tools=[{calls}]" if calls else "")
            )
        lines.append(
            f"total: in={self.total_input_tokens} out={self.total_output_tokens} "
            f"cache_w={self.total_cache_creation_input_tokens} "
            f"cache_r={self.total_cache_read_input_tokens} "
            f"tool_calls={self.tool_call_count} tool_errors={self.tool_error_count} "
            f"latency={self.total_latency_s:.2f}s"
        )
        return "\n".join(lines)
