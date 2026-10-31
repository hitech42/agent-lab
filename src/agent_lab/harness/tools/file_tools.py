"""File tools: list, read, edit and create files inside the sandbox."""

from typing import Any, ClassVar

from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools.base import Tool, ToolError


class ListFilesTool(Tool):
    name = "list_files"
    description = "Recursively list files in the working directory (or a subdirectory)."
    input_schema: ClassVar[dict[str, Any]] = Tool._schema({"path": Tool._path_property()}, [])

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        files = sandbox.list_files(self._opt_str(input, "path") or ".")
        return "\n".join(files) if files else "(no files)"


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file with line numbers. Optionally restrict to start_line..end_line "
        "(1-indexed, inclusive)."
    )
    input_schema: ClassVar[dict[str, Any]] = Tool._schema(
        {
            "path": Tool._path_property(),
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        ["path"],
    )

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        text = sandbox.read_text(self._req_str(input, "path"))
        start = self._opt_int(input, "start_line")
        end = self._opt_int(input, "end_line")
        lines = text.splitlines()
        first = 1 if start is None else start
        last = len(lines) if end is None else end
        if first < 1 or last < first:
            raise ToolError(f"invalid line range {first}-{last} (file has {len(lines)} lines)")
        return "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(lines, 1) if first <= n <= last)


class StrReplaceEditTool(Tool):
    name = "str_replace_edit"
    description = "Replace old_str with new_str in a file. old_str must match exactly once."
    input_schema: ClassVar[dict[str, Any]] = Tool._schema(
        {
            "path": Tool._path_property(),
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
        },
        ["path", "old_str", "new_str"],
    )

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        path = self._req_str(input, "path")
        old = self._req_str(input, "old_str")
        new = self._req_str(input, "new_str")
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


class CreateFileTool(Tool):
    name = "create_file"
    description = "Create a file (or overwrite an existing one) with the given content."
    input_schema: ClassVar[dict[str, Any]] = Tool._schema(
        {"path": Tool._path_property(), "content": {"type": "string"}}, ["path", "content"]
    )

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        path = self._req_str(input, "path")
        existed = sandbox.resolve(path).exists()
        sandbox.write_text(path, self._req_str(input, "content"))
        return f"{'Overwrote' if existed else 'Created'} {path}"
