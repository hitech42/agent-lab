"""The message history of one attempt."""

import copy
from collections.abc import Sequence
from typing import Any


class Conversation:
    """Owns the list of message params sent to the model."""

    def __init__(self, task_prompt: str) -> None:
        self._messages: list[dict[str, Any]] = []
        self.add_user_text(task_prompt)

    def add_user_text(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_assistant(self, blocks: Sequence[Any]) -> None:
        """Append an assistant turn, keeping every block field (lossless)."""
        self._messages.append(
            {"role": "assistant", "content": [self._block_to_param(b) for b in blocks]}
        )

    def add_tool_results(self, results: list[dict[str, Any]]) -> None:
        """Append ONE user message carrying all tool_result blocks."""
        self._messages.append({"role": "user", "content": list(results)})

    def last_assistant_text(self) -> str:
        for message in reversed(self._messages):
            if message["role"] == "assistant":
                return "".join(
                    str(block.get("text"))
                    for block in message["content"]
                    if block.get("type") == "text"
                )
        return ""

    @property
    def messages(self) -> list[dict[str, Any]]:
        """A deep copy; mutating it does not affect the conversation."""
        return copy.deepcopy(self._messages)

    @staticmethod
    def _block_to_param(block: Any) -> dict[str, Any]:
        """Convert a response block (SDK object or dict) to a request param dict.

        All fields are preserved so e.g. thinking blocks round-trip unchanged; only
        None-valued fields are dropped.
        """
        if isinstance(block, dict):
            return {k: v for k, v in block.items() if v is not None}
        dumped: dict[str, Any] = block.model_dump(mode="json", exclude_none=True)
        return dumped
