from typing import Any

import pytest

from agent_lab.harness.sandbox import Sandbox, SandboxError
from agent_lab.harness.tools import (
    CreateFileTool,
    ListFilesTool,
    ReadFileTool,
    StrReplaceEditTool,
    Tool,
    ToolError,
)


def run(tool: Tool, sandbox: Sandbox, **inp: Any) -> str:
    return tool.execute(sandbox, inp)


def test_str_replace_zero_match(sandbox: Sandbox) -> None:
    sandbox.write_text("f.py", "a = 1\n")
    with pytest.raises(ToolError, match="not found"):
        run(StrReplaceEditTool(), sandbox, path="f.py", old_str="zzz", new_str="y")
    assert sandbox.read_text("f.py") == "a = 1\n"


def test_str_replace_multi_match(sandbox: Sandbox) -> None:
    sandbox.write_text("f.py", "x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="2 times"):
        run(StrReplaceEditTool(), sandbox, path="f.py", old_str="x = 1", new_str="y")
    assert sandbox.read_text("f.py") == "x = 1\nx = 1\n"


def test_str_replace_single_match_and_empty_old_str(sandbox: Sandbox) -> None:
    sandbox.write_text("f.py", "x = 1\ny = 2\n")
    run(StrReplaceEditTool(), sandbox, path="f.py", old_str="y = 2", new_str="y = 3")
    assert sandbox.read_text("f.py") == "x = 1\ny = 3\n"
    with pytest.raises(ToolError, match="must not be empty"):
        run(StrReplaceEditTool(), sandbox, path="f.py", old_str="", new_str="q")


def test_read_file_line_ranges(sandbox: Sandbox) -> None:
    sandbox.write_text("f.txt", "one\ntwo\nthree\nfour\n")
    part = run(ReadFileTool(), sandbox, path="f.txt", start_line=2, end_line=3)
    assert "two" in part and "three" in part
    assert "one" not in part and "four" not in part
    whole = run(ReadFileTool(), sandbox, path="f.txt")
    assert "one" in whole and "four" in whole
    with pytest.raises(ToolError, match="invalid line range"):
        run(ReadFileTool(), sandbox, path="f.txt", start_line=0)


def test_create_file_and_list_files(sandbox: Sandbox) -> None:
    assert run(CreateFileTool(), sandbox, path="p/a.py", content="x") == "Created p/a.py"
    assert run(CreateFileTool(), sandbox, path="p/a.py", content="y") == "Overwrote p/a.py"
    assert sandbox.read_text("p/a.py") == "y"
    assert run(ListFilesTool(), sandbox) == "p/a.py"


def test_list_files_empty(sandbox: Sandbox) -> None:
    assert run(ListFilesTool(), sandbox) == "(no files)"


def test_bad_input_types_raise_tool_error(sandbox: Sandbox) -> None:
    sandbox.write_text("f", "line\n")
    with pytest.raises(ToolError, match="'path' is required"):
        run(ReadFileTool(), sandbox, path=5)
    with pytest.raises(ToolError, match="must be an integer"):
        run(ReadFileTool(), sandbox, path="f", start_line=True)
    with pytest.raises(ToolError, match="must be a string"):
        run(ListFilesTool(), sandbox, path=3)


def test_path_escapes_are_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxError):
        run(ReadFileTool(), sandbox, path="../x")
    with pytest.raises(SandboxError):
        run(CreateFileTool(), sandbox, path="/etc/x", content="c")
