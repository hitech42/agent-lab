"""Context management strategies for the agent conversation.

Caching note: with ``AgentConfig.cache_messages`` the agent marks the last block of whatever
a strategy returns (``Agent._with_message_cache``). A strategy that rewrites old messages
changes the prefix from the first changed block onward, so the cache breaks from there:
``TruncateToolOutputs`` changes one more result on every call, ``Summarize`` only when it
writes a new summary. See CLAUDE.md, "Message caching".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_lab.harness.config import AgentConfig
from agent_lab.harness.conversation import Conversation
from agent_lab.harness.model_client import ModelClient, ModelRequest
from agent_lab.harness.tracing import TokenUsage


@dataclass(frozen=True)
class ContextCall:
    """An extra model call made while building the context (data only)."""

    kind: str
    usage: TokenUsage
    stop_reason: str | None
    latency_s: float
    total_latency_s: float
    retries: int


@dataclass(frozen=True)
class ContextResult:
    """The messages for the next API call plus the extra model calls that produced them."""

    messages: list[dict[str, Any]]
    calls: tuple[ContextCall, ...] = ()


class ContextStrategy(ABC):
    """Decides which messages are sent to the model on each call.

    Contract: ``apply(conversation).messages`` is a valid Messages API sequence:

    - it starts with a ``user`` message that contains the task prompt (as its text, or as
      its first text block);
    - roles alternate user, assistant, user, ...;
    - every assistant ``tool_use`` is answered by a ``tool_result`` with the same id in the
      immediately following user message, and no ``tool_result`` is orphaned;
    - the Conversation is never mutated.

    Strategies are shared by all attempts of an agent, so they must not keep per-attempt
    state: ``Agent.run`` calls ``fresh()`` once per attempt and uses the result. A strategy
    that remembers something (a summary) returns a new instance there; the others return
    themselves.
    """

    @abstractmethod
    def apply(self, conversation: Conversation) -> ContextResult:
        """Return the messages for the next API call (and any extra calls it needed)."""

    def fresh(self) -> ContextStrategy:
        """The strategy to use for one attempt (default: this instance, it is stateless)."""
        return self

    @staticmethod
    def from_config(config: AgentConfig, client: ModelClient) -> ContextStrategy:
        """Build the strategy a config asks for; ``client`` is used only by Summarize."""
        return _REGISTRY[config.context_strategy](config, client)

    @staticmethod
    def describe(config: AgentConfig) -> str:
        """``name`` or ``name(param=value, ...)`` for logs."""
        params = [
            f"{name.removeprefix('context_')}={value}"
            for name in (
                "context_keep_last",
                "context_after_turns",
                "context_summary_max_tokens",
            )
            if (value := getattr(config, name)) is not None
        ]
        return config.context_strategy + (f"({', '.join(params)})" if params else "")


class FullHistory(ContextStrategy):
    """Send the whole conversation unchanged."""

    def apply(self, conversation: Conversation) -> ContextResult:
        return ContextResult(conversation.messages)


def _text_of(content: Any) -> str:
    """The text of a tool_result ``content`` (a string or a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _turn_count(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m["role"] == "assistant")


class TruncateToolOutputs(ContextStrategy):
    """Replace tool results older than the last ``keep_last`` assistant turns by a placeholder.

    The placeholder states how many characters were elided and from which tool. The task
    prompt, assistant messages (thinking included) and recent results are unchanged. A pure
    function of the conversation, so an elided result is elided identically on every later
    call. A result already shorter than its placeholder is kept.
    """

    def __init__(self, keep_last: int) -> None:
        if keep_last < 1:
            raise ValueError("keep_last must be at least 1")
        self._keep_last = keep_last

    def apply(self, conversation: Conversation) -> ContextResult:
        messages = conversation.messages
        elide_through = _turn_count(messages) - self._keep_last  # turns 1..this are old
        names: dict[str, str] = {}
        turn = 0
        for message in messages:
            if message["role"] == "assistant":
                turn += 1
                for block in message["content"]:
                    if block.get("type") == "tool_use":
                        names[str(block.get("id"))] = str(block.get("name"))
            elif turn <= elide_through and isinstance(message["content"], list):
                for block in message["content"]:
                    if block.get("type") == "tool_result":
                        self._elide(block, names)
        return ContextResult(messages)

    @staticmethod
    def _elide(block: dict[str, Any], names: dict[str, str]) -> None:
        text = _text_of(block.get("content"))
        tool = names.get(str(block.get("tool_use_id")), "unknown tool")
        placeholder = f"[tool output elided: {len(text)} characters from {tool}]"
        if len(text) > len(placeholder):
            block["content"] = placeholder


_SUMMARY_SYSTEM = (
    "You write a progress summary of an AI coding agent's work so far, so the agent can "
    "continue without the original messages. Be factual and concise. Cover: files read "
    "(and what matters in them), edits made, test results, and open problems or next steps. "
    "Reply with the summary only."
)


class Summarize(ContextStrategy):
    """Replace old turns by a model-written summary once the conversation grows long.

    With ``n`` complete assistant turns and ``covered`` of them already summarized, a summary
    is (re)written when ``n - covered > after_turns``. It covers every turn except the last
    ``keep_last`` (and folds in the previous summary), then ``covered = n - keep_last``.
    Between summaries the previous one is reused, so the sent prefix only grows. The summary
    is placed in the first user message after the task prompt, which keeps roles alternating.
    The summary call has no tools, uses the same model at effort low, and is returned as a
    ``ContextCall`` for the caller to charge. State is per attempt: use ``fresh()``.
    """

    def __init__(
        self,
        after_turns: int,
        keep_last: int,
        summary_max_tokens: int,
        client: ModelClient,
        model: str,
    ) -> None:
        if not (after_turns > keep_last >= 1 and summary_max_tokens >= 1):
            raise ValueError("need after_turns > keep_last >= 1 and summary_max_tokens >= 1")
        self._after_turns = after_turns
        self._keep_last = keep_last
        self._max_tokens = summary_max_tokens
        self._client = client
        self._model = model
        self._covered = 0
        self._summary: str | None = None

    def fresh(self) -> Summarize:
        return Summarize(
            self._after_turns, self._keep_last, self._max_tokens, self._client, self._model
        )

    def apply(self, conversation: Conversation) -> ContextResult:
        messages = conversation.messages
        n = _turn_count(messages)
        calls: tuple[ContextCall, ...] = ()
        if n - self._covered > self._after_turns:
            target = n - self._keep_last
            calls = (self._summarize(messages, target),)
            self._covered = target
        if self._summary is None:
            return ContextResult(messages, calls)
        prompt = messages[0]["content"]
        first = [{"type": "text", "text": prompt}] if isinstance(prompt, str) else list(prompt)
        first.append(
            {
                "type": "text",
                "text": "<progress-summary>\n"
                f"Summary of your work on turns 1-{self._covered} (older messages were removed):\n"
                f"{self._summary}\n</progress-summary>",
            }
        )
        kept = self._messages_after(messages, self._covered)
        return ContextResult([{"role": "user", "content": first}, *kept], calls)

    @staticmethod
    def _messages_after(messages: list[dict[str, Any]], turns: int) -> list[dict[str, Any]]:
        """Messages from assistant turn ``turns + 1`` on (each turn: assistant + its results)."""
        seen = 0
        for index, message in enumerate(messages):
            if message["role"] == "assistant":
                seen += 1
                if seen == turns + 1:
                    return messages[index:]
        return []

    def _summarize(self, messages: list[dict[str, Any]], target: int) -> ContextCall:
        parts = [f"Task given to the agent:\n{messages[0]['content']}"]
        if self._summary is not None:
            parts.append(f"Summary of the work before turn {self._covered + 1}:\n{self._summary}")
        parts.append(self._transcript(messages, self._covered, target))
        parts.append("Write the progress summary now.")
        request = ModelRequest(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": _SUMMARY_SYSTEM}],
            tools=None,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
            effort="low",
        )
        response = self._client.create(request)
        self._summary = "".join(
            str(block.get("text") if isinstance(block, dict) else getattr(block, "text", ""))
            for block in response.content
            if (block.get("type") if isinstance(block, dict) else getattr(block, "type", None))
            == "text"
        ).strip()
        return ContextCall(
            kind="summary",
            usage=response.usage,
            stop_reason=response.stop_reason,
            latency_s=response.latency_s,
            total_latency_s=response.total_latency_s,
            retries=response.retries,
        )

    @staticmethod
    def _transcript(messages: list[dict[str, Any]], start: int, end: int) -> str:
        """Turns ``start + 1 .. end`` as plain text (thinking blocks are skipped)."""
        lines: list[str] = []
        turn = 0
        for message in messages:
            if message["role"] == "assistant":
                turn += 1
            if not start < turn <= end or not isinstance(message["content"], list):
                continue
            for block in message["content"]:
                kind = block.get("type")
                if kind == "text" and message["role"] == "assistant":
                    lines.append(f"[turn {turn}] assistant: {block.get('text')}")
                elif kind == "tool_use":
                    lines.append(f"[turn {turn}] call {block.get('name')}: {block.get('input')}")
                elif kind == "tool_result":
                    lines.append(f"[turn {turn}] result: {_text_of(block.get('content'))}")
        return "Agent transcript:\n" + "\n".join(lines)


_REGISTRY: dict[str, Callable[[AgentConfig, ModelClient], ContextStrategy]] = {
    "full": lambda config, client: FullHistory(),
    "truncate_tool_outputs": lambda config, client: TruncateToolOutputs(
        _required(config.context_keep_last)
    ),
    "summarize": lambda config, client: Summarize(
        _required(config.context_after_turns),
        _required(config.context_keep_last),
        _required(config.context_summary_max_tokens),
        client,
        config.model,
    ),
}


def _required(value: int | None) -> int:
    assert value is not None  # AgentConfig validates the parameters per strategy
    return value
