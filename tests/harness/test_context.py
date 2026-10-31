import pytest

from agent_lab.harness.context import (
    ContextStrategy,
    FullHistory,
    Summarize,
    TruncateToolOutputs,
)
from agent_lab.harness.conversation import Conversation


def test_full_history_returns_everything_unchanged() -> None:
    conv = Conversation("task")
    conv.add_assistant([{"type": "text", "text": "a"}])
    assert FullHistory().apply(conv) == conv.messages


def test_unimplemented_strategies_fail_at_construction() -> None:
    with pytest.raises(NotImplementedError):
        TruncateToolOutputs()
    with pytest.raises(NotImplementedError):
        Summarize()


def test_from_name() -> None:
    assert isinstance(ContextStrategy.from_name("full"), FullHistory)
    for name in ("truncate_tool_outputs", "summarize"):
        with pytest.raises(NotImplementedError):
            ContextStrategy.from_name(name)  # type: ignore[arg-type]
