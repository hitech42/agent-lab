"""Tools that execute commands inside the sandbox."""

from typing import Any, ClassVar

from agent_lab.harness.sandbox import CommandResult, Sandbox
from agent_lab.harness.tools.base import Tool, ToolError

_TEST_OUTPUT_TAIL_CHARS = 4000


class RunTestsTool(Tool):
    name = "run_tests"
    description = "Run pytest on the visible tests in the working directory."
    input_schema: ClassVar[dict[str, Any]] = Tool._schema({}, [])

    def __init__(self, timeout_s: float = 60.0) -> None:
        self._timeout_s = timeout_s

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        result = sandbox.run_command(
            "python -m pytest -q -x --no-header -p no:cacheprovider", self._timeout_s
        )
        if result.timed_out:
            raise ToolError(result.output)
        return (
            f"{self._tail(result.output, _TEST_OUTPUT_TAIL_CHARS)}\n[exit code: {result.exit_code}]"
        )

    @staticmethod
    def _tail(text: str, n: int) -> str:
        if len(text) <= n:
            return text
        return f"[... {len(text) - n} earlier chars trimmed ...]\n{text[-n:]}"


class BashTool(Tool):
    name = "bash"
    input_schema: ClassVar[dict[str, Any]] = Tool._schema(
        {"command": {"type": "string"}}, ["command"]
    )

    def __init__(self, timeout_s: float = 60.0) -> None:
        self._timeout_s = timeout_s
        self.description = f"Run a shell command in the working directory ({timeout_s:g}s timeout)."

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        result = sandbox.run_command(self._req_str(input, "command"), self._timeout_s)
        return self._format(result)

    @staticmethod
    def _format(result: CommandResult) -> str:
        if result.timed_out:
            raise ToolError(result.output)
        return f"{result.output}\n[exit code: {result.exit_code}]"
