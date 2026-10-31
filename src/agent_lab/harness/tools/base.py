"""Tool base class and shared result types."""

import copy
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from agent_lab.harness.sandbox import Sandbox


class ToolError(Exception):
    """A tool failure to report to the model (becomes an is_error tool_result)."""


class ToolOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    is_error: bool


class Tool(ABC):
    """One capability offered to the model."""

    name: ClassVar[str]
    description: str  # a class attribute, or set per instance when it depends on config
    input_schema: ClassVar[dict[str, Any]]

    @abstractmethod
    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        """Run the tool. Raise ToolError (or any exception) to report a failure."""

    def to_api_param(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": copy.deepcopy(self.input_schema),
        }

    @staticmethod
    def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": required}

    @staticmethod
    def _req_str(inp: dict[str, Any], key: str) -> str:
        value = inp.get(key)
        if not isinstance(value, str):
            raise ToolError(f"'{key}' is required and must be a string")
        return value

    @staticmethod
    def _opt_str(inp: dict[str, Any], key: str) -> str | None:
        value = inp.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ToolError(f"'{key}' must be a string")
        return value

    @staticmethod
    def _opt_int(inp: dict[str, Any], key: str) -> int | None:
        value = inp.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolError(f"'{key}' must be an integer")
        return value
