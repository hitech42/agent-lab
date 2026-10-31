"""Tool registry, handlers and safe execution for the agent."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_lab.harness.config import ToolSet
from agent_lab.harness.redact import redact
from agent_lab.harness.sandbox import CommandResult, Sandbox

DEFAULT_COMMAND_TIMEOUT_S = 60.0
_TEST_OUTPUT_TAIL_CHARS = 4000

Handler = Callable[[Sandbox, dict[str, Any]], str]


class ToolError(Exception):
    """A tool failure to report to the model (becomes an is_error tool_result)."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    is_error: bool


def truncate(text: str, max_chars: int) -> str:
    """Cut text to max_chars, appending a visible marker saying how much was dropped."""
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return f"{text[:max_chars]}\n[... output truncated: {dropped} more chars ...]"


def _tail(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return f"[... {len(text) - n} earlier chars trimmed ...]\n{text[-n:]}"


# --- input helpers -------------------------------------------------------------------


def _req_str(inp: dict[str, Any], key: str) -> str:
    value = inp.get(key)
    if not isinstance(value, str):
        raise ToolError(f"'{key}' is required and must be a string")
    return value


def _opt_str(inp: dict[str, Any], key: str) -> str | None:
    value = inp.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string")
    return value


def _opt_int(inp: dict[str, Any], key: str) -> int | None:
    value = inp.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"'{key}' must be an integer")
    return value


def _format_command(result: CommandResult) -> str:
    if result.timed_out:
        raise ToolError(result.output)
    return f"{result.output}\n[exit code: {result.exit_code}]"


# --- handlers ------------------------------------------------------------------------


def _list_files(sandbox: Sandbox, inp: dict[str, Any]) -> str:
    files = sandbox.list_files(_opt_str(inp, "path") or ".")
    return "\n".join(files) if files else "(no files)"


def _read_file(sandbox: Sandbox, inp: dict[str, Any]) -> str:
    text = sandbox.read_text(_req_str(inp, "path"))
    start = _opt_int(inp, "start_line")
    end = _opt_int(inp, "end_line")
    lines = text.splitlines()
    first = 1 if start is None else start
    last = len(lines) if end is None else end
    if first < 1 or last < first:
        raise ToolError(f"invalid line range {first}-{last} (file has {len(lines)} lines)")
    return "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(lines, 1) if first <= n <= last)


def _str_replace_edit(sandbox: Sandbox, inp: dict[str, Any]) -> str:
    path = _req_str(inp, "path")
    old = _req_str(inp, "old_str")
    new = _req_str(inp, "new_str")
    if old == "":
        raise ToolError("old_str must not be empty")
    text = sandbox.read_text(path)
    count = text.count(old)
    if count == 0:
        raise ToolError(f"old_str not found in {path}; no changes made")
    if count > 1:
        raise ToolError(
            f"old_str matches {count} times in {path}; it must match exactly once. "
            "Include more surrounding context. No changes made."
        )
    sandbox.write_text(path, text.replace(old, new, 1))
    return f"Edited {path}"


def _create_file(sandbox: Sandbox, inp: dict[str, Any]) -> str:
    path = _req_str(inp, "path")
    existed = sandbox.resolve(path).exists()
    sandbox.write_text(path, _req_str(inp, "content"))
    return f"{'Overwrote' if existed else 'Created'} {path}"


def _run_tests(sandbox: Sandbox, inp: dict[str, Any]) -> str:
    result = sandbox.run_command(
        "python -m pytest -q -x --no-header -p no:cacheprovider", DEFAULT_COMMAND_TIMEOUT_S
    )
    if result.timed_out:
        raise ToolError(result.output)
    return f"{_tail(result.output, _TEST_OUTPUT_TAIL_CHARS)}\n[exit code: {result.exit_code}]"


def _bash(sandbox: Sandbox, inp: dict[str, Any]) -> str:
    return _format_command(sandbox.run_command(_req_str(inp, "command"), DEFAULT_COMMAND_TIMEOUT_S))


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


_PATH = {"type": "string", "description": "Path relative to the working directory."}

STRUCTURED_TOOLS: tuple[Tool, ...] = (
    Tool(
        "list_files",
        "Recursively list files in the working directory (or a subdirectory).",
        _schema({"path": _PATH}, []),
        _list_files,
    ),
    Tool(
        "read_file",
        "Read a file with line numbers. Optionally restrict to start_line..end_line "
        "(1-indexed, inclusive).",
        _schema(
            {
                "path": _PATH,
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            ["path"],
        ),
        _read_file,
    ),
    Tool(
        "str_replace_edit",
        "Replace old_str with new_str in a file. old_str must match exactly once.",
        _schema(
            {"path": _PATH, "old_str": {"type": "string"}, "new_str": {"type": "string"}},
            ["path", "old_str", "new_str"],
        ),
        _str_replace_edit,
    ),
    Tool(
        "create_file",
        "Create a file (or overwrite an existing one) with the given content.",
        _schema({"path": _PATH, "content": {"type": "string"}}, ["path", "content"]),
        _create_file,
    ),
    Tool(
        "run_tests",
        "Run pytest on the visible tests in the working directory.",
        _schema({}, []),
        _run_tests,
    ),
)

BASH_ONLY_TOOLS: tuple[Tool, ...] = (
    Tool(
        "bash",
        "Run a shell command in the working directory (60s timeout).",
        _schema({"command": {"type": "string"}}, ["command"]),
        _bash,
    ),
)


def get_tools(tool_set: ToolSet) -> list[Tool]:
    return list(STRUCTURED_TOOLS if tool_set == "structured" else BASH_ONLY_TOOLS)


def to_api_tool_params(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def execute_tool(
    tool: Tool | None, sandbox: Sandbox, raw_input: Any, max_chars: int
) -> ToolOutcome:
    """Run a tool and return its outcome. Never raises.

    Output is redacted of secrets first, then truncated to ``max_chars``.
    """
    try:
        if tool is None:
            raise ToolError("unknown tool")
        if not isinstance(raw_input, dict):
            raise ToolError("tool input must be a JSON object")
        content, is_error = tool.handler(sandbox, raw_input), False
    except ToolError as exc:
        content, is_error = f"Error: {exc}", True
    except Exception as exc:  # tool failures go to the model, never crash the loop
        content, is_error = f"Error: {type(exc).__name__}: {exc}", True
    return ToolOutcome(truncate(redact(content), max_chars), is_error)
