"""Hand-written agent loop on the Anthropic Messages API.

The harness owns retries: construct the real client with ``max_retries=0`` so the
SDK's built-in retries don't stack on top of ours.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel, Field

from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.context import apply_context_strategy
from agent_lab.harness.redact import redact, redact_obj
from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools import execute_tool, get_tools, to_api_tool_params
from agent_lab.harness.tracing import AttemptTrace, TokenUsage, ToolCallTrace, TurnTrace

MAX_ATTEMPTS = 3
BASE_DELAY_S = 1.0
_CACHE = {"type": "ephemeral"}

AttemptStatus = Literal["completed", "max_turns", "budget_exceeded", "error"]


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
    """What run_agent needs from a client; anthropic.Anthropic satisfies it."""

    @property
    def messages(self) -> MessagesAPI: ...


class AttemptResult(BaseModel):
    status: AttemptStatus
    stop_reason: str | None
    final_text: str
    trace: AttemptTrace
    cost_usd: float | None
    error: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return True
    return isinstance(exc, anthropic.APIStatusError) and (
        exc.status_code == 429 or exc.status_code >= 500
    )


@dataclass(frozen=True)
class RetryResult[T]:
    value: T
    retries: int
    last_call_s: float  # duration of the successful call only


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    max_attempts: int = MAX_ATTEMPTS,
    base_delay: float = BASE_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> RetryResult[T]:
    """Call fn, retrying transient errors with exponential backoff.

    Re-raises the last error when attempts run out, or immediately for
    non-transient errors.
    """
    for attempt in range(max_attempts):
        started = time.perf_counter()
        try:
            value = fn()
        except Exception as exc:
            if not is_transient(exc) or attempt == max_attempts - 1:
                raise
            sleep(base_delay * 2**attempt)
        else:
            return RetryResult(value, attempt, time.perf_counter() - started)
    raise AssertionError("unreachable")


def _block_type(block: Any) -> str | None:
    t = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
    return t if isinstance(t, str) else None


def _field(block: Any, name: str) -> Any:
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


def block_to_param(block: Any) -> dict[str, Any]:
    """Losslessly convert a response content block to a request param dict.

    All fields are preserved (so e.g. thinking blocks round-trip unchanged); only
    None-valued fields are dropped.
    """
    if isinstance(block, dict):
        return {k: v for k, v in block.items() if v is not None}
    dumped: dict[str, Any] = block.model_dump(mode="json", exclude_none=True)
    return dumped


def _text_of(blocks: Sequence[Any]) -> str:
    return "".join(str(_field(b, "text")) for b in blocks if _block_type(b) == "text")


def _usage_of(usage: UsageLike) -> TokenUsage:
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
    )


def run_agent(
    task_prompt: str,
    config: AgentConfig,
    sandbox: Sandbox,
    client: MessagesClient,
    budget: Budget,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> AttemptResult:
    tools = get_tools(config.tool_set)
    tool_map = {t.name: t for t in tools}
    api_tools = to_api_tool_params(tools)
    api_tools[-1]["cache_control"] = dict(_CACHE)
    system = [{"type": "text", "text": config.system_prompt, "cache_control": dict(_CACHE)}]

    messages: list[dict[str, Any]] = [{"role": "user", "content": task_prompt}]
    apply_context_strategy(config.context_strategy, messages)  # fail fast, before any spend

    turns: list[TurnTrace] = []
    status: AttemptStatus = "max_turns"
    stop_reason: str | None = None
    error: str | None = None

    for turn in range(1, config.max_turns + 1):
        request_messages = apply_context_strategy(config.context_strategy, messages)
        started = time.perf_counter()
        try:
            call = call_with_retry(
                partial(
                    client.messages.create,
                    model=config.model,
                    max_tokens=config.max_tokens_per_call,
                    system=system,
                    tools=api_tools,
                    messages=request_messages,
                ),
                sleep=sleep,
            )
        except Exception as exc:  # run_agent never raises; record and stop
            status, error = "error", f"{type(exc).__name__}: {exc}"
            break
        total_latency = time.perf_counter() - started
        response = call.value

        usage = _usage_of(response.usage)
        budget.record(usage)
        stop_reason = response.stop_reason
        content = list(response.content)
        messages.append({"role": "assistant", "content": [block_to_param(b) for b in content]})
        trace = TurnTrace(
            turn=turn,
            **usage.model_dump(),
            stop_reason=stop_reason,
            latency_s=call.last_call_s,
            total_latency_s=total_latency,
            retries=call.retries,
        )
        turns.append(trace)

        if stop_reason != "tool_use":
            status = "completed"
            break

        tool_blocks = [b for b in content if _block_type(b) == "tool_use"]
        if not tool_blocks:
            status, error = "error", "stop_reason was tool_use but no tool_use blocks were returned"
            break
        if budget.exceeded():
            status = "budget_exceeded"
            break

        results: list[dict[str, Any]] = []
        for block in tool_blocks:
            name = str(_field(block, "name"))
            t0 = time.perf_counter()
            outcome = execute_tool(
                tool_map.get(name), sandbox, _field(block, "input"), config.tool_output_max_chars
            )
            trace.tool_calls.append(
                ToolCallTrace(
                    name=name, duration_s=time.perf_counter() - t0, is_error=outcome.is_error
                )
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": _field(block, "id"),
                    "content": outcome.content,
                    "is_error": outcome.is_error,
                }
            )
        messages.append({"role": "user", "content": results})

    final_text = next(
        (_text_of(m["content"]) for m in reversed(messages) if m["role"] == "assistant"), ""
    )

    return AttemptResult(
        status=status,
        stop_reason=stop_reason,
        final_text=redact(final_text),
        trace=AttemptTrace(turns=turns),
        cost_usd=budget.cost_usd,
        error=None if error is None else redact(error),
        messages=redact_obj(messages),
    )
