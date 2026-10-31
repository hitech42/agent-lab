"""Tool registry: look up, describe and safely execute the tools offered to the model."""

from typing import Any

from agent_lab.harness.config import ToolSet
from agent_lab.harness.redact import Redactor
from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools.base import Tool, ToolError, ToolOutcome
from agent_lab.harness.tools.exec_tools import BashTool, RunTestsTool
from agent_lab.harness.tools.file_tools import (
    CreateFileTool,
    ListFilesTool,
    ReadFileTool,
    StrReplaceEditTool,
)


class ToolRegistry:
    def __init__(self, tools: list[Tool], redactor: Redactor, max_output_chars: int) -> None:
        self._tools = {t.name: t for t in tools}
        self._order = [t.name for t in tools]
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @classmethod
    def for_set(cls, name: ToolSet, redactor: Redactor, max_output_chars: int) -> ToolRegistry:
        tools: list[Tool]
        match name:
            case "structured":
                tools = [
                    ListFilesTool(),
                    ReadFileTool(),
                    StrReplaceEditTool(),
                    CreateFileTool(),
                    RunTestsTool(),
                ]
            case "bash_only":
                tools = [BashTool()]
        return cls(tools, redactor, max_output_chars)

    def api_params(self) -> list[dict[str, Any]]:
        """Tool definitions for the API, with cache_control on the last one only."""
        params = [self._tools[n].to_api_param() for n in self._order]
        if params:
            params[-1]["cache_control"] = {"type": "ephemeral"}
        return params

    def execute(self, name: str, input: Any, sandbox: Sandbox) -> ToolOutcome:
        """Run a tool and return its outcome. Never raises.

        Output is redacted of secrets first, then truncated to the max length.
        """
        try:
            tool = self._tools.get(name)
            if tool is None:
                raise ToolError("unknown tool")
            if not isinstance(input, dict):
                raise ToolError("tool input must be a JSON object")
            content, is_error = tool.execute(sandbox, input), False
        except ToolError as exc:
            content, is_error = f"Error: {exc}", True
        except Exception as exc:  # tool failures go to the model, never crash the loop
            content, is_error = f"Error: {type(exc).__name__}: {exc}", True
        return ToolOutcome(
            content=self._truncate(self._redactor.redact(content), self._max_output_chars),
            is_error=is_error,
        )

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        """Cut text to max_chars, appending a visible marker of how much was dropped."""
        if len(text) <= max_chars:
            return text
        dropped = len(text) - max_chars
        return f"{text[:max_chars]}\n[... output truncated: {dropped} more chars ...]"
