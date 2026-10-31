"""Model access: retry policy plus a thin wrapper over the Messages API client.

The harness owns retries: construct the real client with ``max_retries=0`` so the
SDK's built-in retries don't stack on top of ours.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from agent_lab.harness.tracing import TokenUsage


class UsageLike(Protocol):
    @property
    def input_tokens(self) -> int: ...
    @property
    def output_tokens(self) -> int: ...
    @property
    def cache_creation_input_tokens(self) -> int | None: ...
    @property
    def cache_read_input_tokens(self) -> int | None: ...


class ResponseLike(Protocol):
    @property
    def content(self) -> Sequence[Any]: ...
    @property
    def stop_reason(self) -> str | None: ...
    @property
    def usage(self) -> UsageLike: ...


class MessagesAPI(Protocol):
    def create(
        self, *, model: str, max_tokens: int, system: Any, tools: Any, messages: Any
    ) -> ResponseLike: ...


class MessagesClient(Protocol):
    """What ModelClient needs from a raw client; anthropic.Anthropic satisfies it."""

    @property
    def messages(self) -> MessagesAPI: ...


@dataclass(frozen=True)
class ModelRequest:
    model: str
    max_tokens: int
    system: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass(frozen=True)
class ModelResponse:
    """What the agent needs from one model call (data only)."""

    stop_reason: str | None
    content: list[Any]
    usage: TokenUsage
    tool_calls: list[ToolCall]
    retries: int
    latency_s: float  # duration of the successful call only
    total_latency_s: float  # failed attempts, backoff sleeps and the successful call


class RetryPolicy:
    """Exponential backoff for transient API errors."""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._sleep = sleep

    @staticmethod
    def is_transient(exc: Exception) -> bool:
        if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
            return True
        return isinstance(exc, anthropic.APIStatusError) and (
            exc.status_code == 429 or exc.status_code >= 500
        )

    def call[T](self, fn: Callable[[], T]) -> tuple[T, int]:
        """Call fn, retrying transient errors. Returns (result, retries_used).

        Re-raises the last error when attempts run out, or immediately for
        non-transient errors.
        """
        for attempt in range(self._max_attempts):
            try:
                return fn(), attempt
            except Exception as exc:
                if not self.is_transient(exc) or attempt == self._max_attempts - 1:
                    raise
                self._sleep(self._base_delay * 2**attempt)
        raise AssertionError("unreachable")


class ModelClient:
    def __init__(self, raw_client: MessagesClient, retry_policy: RetryPolicy) -> None:
        self._raw = raw_client
        self._retry = retry_policy

    def create(self, request: ModelRequest) -> ModelResponse:
        """Send the request, retrying transient errors. Raises when retries run out."""
        last_call_s = 0.0

        def attempt() -> ResponseLike:
            nonlocal last_call_s
            started = time.perf_counter()
            response = self._raw.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                tools=request.tools,
                messages=request.messages,
            )
            last_call_s = time.perf_counter() - started
            return response

        started = time.perf_counter()
        response, retries = self._retry.call(attempt)
        return ModelResponse(
            stop_reason=response.stop_reason,
            content=list(response.content),
            usage=self._usage_of(response),
            tool_calls=self._tool_calls_of(response),
            retries=retries,
            latency_s=last_call_s,
            total_latency_s=time.perf_counter() - started,
        )

    @staticmethod
    def _usage_of(response: ResponseLike) -> TokenUsage:
        u = response.usage
        return TokenUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            cache_read_input_tokens=u.cache_read_input_tokens or 0,
        )

    @staticmethod
    def _tool_calls_of(response: ResponseLike) -> list[ToolCall]:
        return [
            ToolCall(
                id=ModelClient._field(b, "id"),
                name=str(ModelClient._field(b, "name")),
                input=ModelClient._field(b, "input"),
            )
            for b in response.content
            if ModelClient._field(b, "type") == "tool_use"
        ]

    @staticmethod
    def _field(block: Any, name: str) -> Any:
        return block.get(name) if isinstance(block, dict) else getattr(block, name, None)
