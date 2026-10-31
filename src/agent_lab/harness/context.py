"""Context management strategies for the agent conversation."""

from typing import Any

from agent_lab.harness.config import ContextStrategy


def apply_context_strategy(
    strategy: ContextStrategy, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the messages to send on the next API call.

    Only "full" (send everything unchanged) is implemented so far.
    """
    if strategy == "full":
        return messages
    raise NotImplementedError(f"context strategy {strategy!r} is not implemented yet")
