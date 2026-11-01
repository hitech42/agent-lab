"""Shared fakes and builders for harness tests. Nothing here touches the network."""

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from agent_lab.harness.config import AgentConfig
from agent_lab.harness.sandbox import Sandbox


def usage(i: int = 10, o: int = 5, cw: int | None = None, cr: int | None = None) -> Usage:
    return Usage(
        input_tokens=i,
        output_tokens=o,
        cache_creation_input_tokens=cw,
        cache_read_input_tokens=cr,
    )


def _message(content: list[Any], stop_reason: str, u: Usage) -> Message:
    return Message(
        id="msg_test",
        content=content,
        model="fake",
        role="assistant",
        stop_reason=stop_reason,  # type: ignore[arg-type]
        type="message",
        usage=u,
    )


def text_response(text: str = "done", u: Usage | None = None) -> Message:
    return _message([TextBlock(type="text", text=text)], "end_turn", u or usage())


def stopped_response(stop_reason: str, text: str = "partial") -> Message:
    """A reply that ended for the given stop reason."""
    return _message([TextBlock(type="text", text=text)], stop_reason, usage())


def tool_use_response(
    *calls: tuple[str, str, dict[str, Any]], text: str | None = None, u: Usage | None = None
) -> Message:
    """Each call is (tool_use_id, tool_name, input)."""
    blocks: list[Any] = [TextBlock(type="text", text=text)] if text else []
    blocks += [ToolUseBlock(type="tool_use", id=i, name=n, input=inp) for i, n, inp in calls]
    return _message(blocks, "tool_use", u or usage())


def status_error(code: int, headers: dict[str, str] | None = None) -> anthropic.APIStatusError:
    response = httpx2.Response(
        code, headers=headers, request=httpx2.Request("POST", "https://example.invalid")
    )
    return anthropic.APIStatusError("boom", response=response, body=None)


class _FakeMessages:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(copy.deepcopy(kwargs))
        if not self.script:
            raise AssertionError("FakeClient script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    """Replays scripted responses (or raises scripted exceptions) in order."""

    def __init__(self, script: list[Any]) -> None:
        self.messages = _FakeMessages(script)

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.messages.requests


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[Sandbox]:
    with Sandbox(tmp_path) as sb:
        yield sb


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig(
        name="test",
        model="claude-haiku-5-5",
        system_prompt="You are a test agent.",
        effort="medium",
        tool_set="structured",
        max_turns=10,
        max_tokens_per_call=1000,
        tool_output_max_chars=2000,
    )
