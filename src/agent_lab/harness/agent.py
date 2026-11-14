"""The hand-written agent loop on the Anthropic Messages API."""

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.context import ContextStrategy
from agent_lab.harness.conversation import Conversation
from agent_lab.harness.model_client import ModelClient, ModelRequest, ToolCall
from agent_lab.harness.redact import Redactor
from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools import ToolRegistry
from agent_lab.harness.tracing import AttemptTrace, ContextCallTrace, ToolCallTrace, TurnTrace

AttemptStatus = Literal[
    "completed", "truncated", "refused", "incomplete", "max_turns", "budget_exceeded", "error"
]

_COMPLETED_STOPS = frozenset({"end_turn", "stop_sequence"})
_TRUNCATED_STOPS = frozenset({"max_tokens", "model_context_window_exceeded"})
_UNCACHEABLE_BLOCKS = frozenset({"thinking", "redacted_thinking"})  # cannot carry cache_control


class AttemptResult(BaseModel):
    """Outcome of one attempt (data only)."""

    status: AttemptStatus
    stop_reason: str | None
    final_text: str
    trace: AttemptTrace
    cost_usd: float | None
    error: str | None = None
    error_type: str | None = None  # exception class name, set together with ``error``
    error_status: int | None = None  # HTTP status when the exception carries one
    messages: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class _Attempt:
    """Mutable state of one run; lives only for the duration of ``Agent.run``."""

    conversation: Conversation
    turns: list[TurnTrace] = field(default_factory=list)
    context_calls: list[ContextCallTrace] = field(default_factory=list)
    status: AttemptStatus = "max_turns"
    stop_reason: str | None = None
    error: str | None = None
    error_type: str | None = None
    error_status: int | None = None


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        client: ModelClient,
        tools: ToolRegistry,
        context: ContextStrategy,
        redactor: Redactor,
    ) -> None:
        self._config = config
        self._client = client
        self._tools = tools
        self._context = context
        self._redactor = redactor

    @classmethod
    def from_config(cls, config: AgentConfig, client: ModelClient, redactor: Redactor) -> Agent:
        """Build the tool registry and context strategy a config asks for."""
        return cls(
            config,
            client,
            ToolRegistry.for_set(config.tool_set, redactor, config.tool_output_max_chars),
            ContextStrategy.from_config(config, client),
            redactor,
        )

    def run(self, task_prompt: str, sandbox: Sandbox, budget: Budget) -> AttemptResult:
        """Run one attempt. Never raises; failures are recorded in the result."""
        attempt = _Attempt(conversation=Conversation(task_prompt))
        context = self._context.fresh()  # per-attempt state (e.g. a summary) lives there

        for turn in range(1, self._config.max_turns + 1):
            try:
                request = self._build_request(attempt, context, turn, budget)
                response = self._client.create(request)
            except Exception as exc:  # run never raises; record and stop
                attempt.status, attempt.error = "error", f"{type(exc).__name__}: {exc}"
                attempt.error_type = type(exc).__name__
                attempt.error_status = getattr(exc, "status_code", None)
                break

            usage = response.usage
            budget.record(usage)
            attempt.stop_reason = response.stop_reason
            attempt.conversation.add_assistant(response.content)
            trace = TurnTrace(
                turn=turn,
                **usage.model_dump(),
                stop_reason=response.stop_reason,
                latency_s=response.latency_s,
                total_latency_s=response.total_latency_s,
                retries=response.retries,
            )
            attempt.turns.append(trace)

            if response.stop_reason != "tool_use":
                attempt.status = self._status_for(response.stop_reason)
                break
            calls = response.tool_calls
            if not calls:
                attempt.status = "error"
                attempt.error = "stop_reason was tool_use but no tool_use blocks were returned"
                attempt.error_type = "InvalidResponse"
                break
            if budget.exceeded():
                attempt.status = "budget_exceeded"
                break

            self._execute_tool_calls(calls, sandbox, trace, attempt.conversation)

        return self._assemble_result(attempt, budget)

    @staticmethod
    def _status_for(stop_reason: str | None) -> AttemptStatus:
        """The status of an attempt whose last reply was not a tool call.

        Only a reply that ended on its own is ``completed``; a reply cut off by a token or
        context limit is ``truncated``, a refusal is ``refused``, and anything else
        (``pause_turn``, a missing or unknown reason) is ``incomplete``.
        """
        if stop_reason in _COMPLETED_STOPS:
            return "completed"
        if stop_reason in _TRUNCATED_STOPS:
            return "truncated"
        if stop_reason == "refusal":
            return "refused"
        return "incomplete"

    def warmup_request(self) -> ModelRequest:
        """A minimal request whose cached prefix (system + tools) matches a real attempt's."""
        return self._request([{"role": "user", "content": "ok"}], max_tokens=1)

    def _build_request(
        self, attempt: _Attempt, context: ContextStrategy, turn: int, budget: Budget
    ) -> ModelRequest:
        """The request for model call ``turn``; extra calls the context made are charged here."""
        result = context.apply(attempt.conversation)
        for call in result.calls:
            budget.record(call.usage)
            attempt.context_calls.append(
                ContextCallTrace(
                    kind=call.kind,
                    after_turn=turn,
                    **call.usage.model_dump(),
                    stop_reason=call.stop_reason,
                    latency_s=call.latency_s,
                    total_latency_s=call.total_latency_s,
                    retries=call.retries,
                )
            )
        messages = result.messages
        if self._config.cache_messages:
            messages = self._with_message_cache(messages)
        return self._request(messages, max_tokens=self._config.max_tokens_per_call)

    @staticmethod
    def _with_message_cache(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A copy of ``messages`` with ``cache_control`` on the last block of the last message.

        Only the copy changes: the last message, its content list and its last block are new
        objects, so neither the context strategy's output nor the Conversation is touched. A
        plain-string prompt becomes one text block to carry the marker. A last block that cannot
        carry it (a thinking block, an empty text block) is left alone: the request goes out
        unmarked rather than failing. Together with the system prompt and the last tool this is
        three breakpoints; the API allows four.
        """
        if not messages:
            return messages
        last = messages[-1]
        content = last["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
        if not blocks:
            return messages
        tail = blocks[-1]
        if tail.get("type") in _UNCACHEABLE_BLOCKS or (
            tail.get("type") == "text" and not tail.get("text")
        ):
            return messages
        blocks[-1] = {**tail, "cache_control": {"type": "ephemeral"}}
        return [*messages[:-1], {**last, "content": blocks}]

    def _request(self, messages: list[dict[str, Any]], max_tokens: int) -> ModelRequest:
        cache = {"type": "ephemeral"}
        return ModelRequest(
            model=self._config.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": self._config.system_prompt, "cache_control": cache}],
            tools=self._tools.api_params(),
            messages=messages,
            effort=self._config.effort,
        )

    def _execute_tool_calls(
        self,
        calls: list[ToolCall],
        sandbox: Sandbox,
        trace: TurnTrace,
        conversation: Conversation,
    ) -> None:
        """Run every tool call; return all results in ONE user message, in order."""
        results: list[dict[str, Any]] = []
        for call in calls:
            started = time.perf_counter()
            outcome = self._tools.execute(call.name, call.input, sandbox)
            trace.tool_calls.append(
                ToolCallTrace(
                    name=call.name,
                    duration_s=time.perf_counter() - started,
                    is_error=outcome.is_error,
                )
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": outcome.content,
                    "is_error": outcome.is_error,
                }
            )
        conversation.add_tool_results(results)

    def _assemble_result(self, attempt: _Attempt, budget: Budget) -> AttemptResult:
        error = attempt.error
        return AttemptResult(
            status=attempt.status,
            stop_reason=attempt.stop_reason,
            final_text=self._redactor.redact(attempt.conversation.last_assistant_text()),
            trace=AttemptTrace(turns=attempt.turns, context_calls=attempt.context_calls),
            cost_usd=budget.cost_usd,
            error=None if error is None else self._redactor.redact(error),
            error_type=attempt.error_type,
            error_status=attempt.error_status,
            messages=self._redactor.redact_obj(attempt.conversation.messages),
        )
