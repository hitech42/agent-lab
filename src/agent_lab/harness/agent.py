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
from agent_lab.harness.tracing import AttemptTrace, ToolCallTrace, TurnTrace

AttemptStatus = Literal["completed", "max_turns", "budget_exceeded", "error"]


class AttemptResult(BaseModel):
    """Outcome of one attempt (data only)."""

    status: AttemptStatus
    stop_reason: str | None
    final_text: str
    trace: AttemptTrace
    cost_usd: float | None
    error: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class _Attempt:
    """Mutable state of one run; lives only for the duration of ``Agent.run``."""

    conversation: Conversation
    turns: list[TurnTrace] = field(default_factory=list)
    status: AttemptStatus = "max_turns"
    stop_reason: str | None = None
    error: str | None = None


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
        """Build the tool registry and context strategy a config asks for.

        Raises NotImplementedError for a context strategy that is not implemented yet.
        """
        return cls(
            config,
            client,
            ToolRegistry.for_set(config.tool_set, redactor, config.tool_output_max_chars),
            ContextStrategy.from_name(config.context_strategy),
            redactor,
        )

    def run(self, task_prompt: str, sandbox: Sandbox, budget: Budget) -> AttemptResult:
        """Run one attempt. Never raises; failures are recorded in the result."""
        attempt = _Attempt(conversation=Conversation(task_prompt))

        for turn in range(1, self._config.max_turns + 1):
            try:
                response = self._client.create(self._build_request(attempt.conversation))
            except Exception as exc:  # run never raises; record and stop
                attempt.status, attempt.error = "error", f"{type(exc).__name__}: {exc}"
                break

            usage = response.usage()
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
                attempt.status = "completed"
                break
            calls = response.tool_calls()
            if not calls:
                attempt.status = "error"
                attempt.error = "stop_reason was tool_use but no tool_use blocks were returned"
                break
            if budget.exceeded():
                attempt.status = "budget_exceeded"
                break

            self._execute_tool_calls(calls, sandbox, trace, attempt.conversation)

        return self._assemble_result(attempt, budget)

    def _build_request(self, conversation: Conversation) -> ModelRequest:
        cache = {"type": "ephemeral"}
        return ModelRequest(
            model=self._config.model,
            max_tokens=self._config.max_tokens_per_call,
            system=[{"type": "text", "text": self._config.system_prompt, "cache_control": cache}],
            tools=self._tools.api_params(),
            messages=self._context.apply(conversation),
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
            trace=AttemptTrace(turns=attempt.turns),
            cost_usd=budget.cost_usd,
            error=None if error is None else self._redactor.redact(error),
            messages=self._redactor.redact_obj(attempt.conversation.messages),
        )
