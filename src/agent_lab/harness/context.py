"""Context management strategies for the agent conversation."""

from abc import ABC, abstractmethod
from typing import Any

from agent_lab.harness.config import ContextStrategyName
from agent_lab.harness.conversation import Conversation


class ContextStrategy(ABC):
    """Decides which messages are sent to the model on each call."""

    @abstractmethod
    def apply(self, conversation: Conversation) -> list[dict[str, Any]]:
        """Return the message params for the next API call."""

    @staticmethod
    def from_name(name: ContextStrategyName) -> ContextStrategy:
        match name:
            case "full":
                return FullHistory()
            case "truncate_tool_outputs":
                return TruncateToolOutputs()
            case "summarize":
                return Summarize()


class FullHistory(ContextStrategy):
    """Send the whole conversation unchanged."""

    def apply(self, conversation: Conversation) -> list[dict[str, Any]]:
        return conversation.messages


class TruncateToolOutputs(ContextStrategy):
    """Stub: shrink old tool outputs. Fails fast at construction."""

    def __init__(self) -> None:
        raise NotImplementedError("context strategy 'truncate_tool_outputs' is not implemented yet")

    def apply(self, conversation: Conversation) -> list[dict[str, Any]]:
        raise NotImplementedError


class Summarize(ContextStrategy):
    """Stub: summarize old turns. Fails fast at construction."""

    def __init__(self) -> None:
        raise NotImplementedError("context strategy 'summarize' is not implemented yet")

    def apply(self, conversation: Conversation) -> list[dict[str, Any]]:
        raise NotImplementedError
