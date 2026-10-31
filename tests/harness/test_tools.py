import pytest

from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools import (
    BASH_ONLY_TOOLS,
    STRUCTURED_TOOLS,
    Tool,
    execute_tool,
    get_tools,
    to_api_tool_params,
    truncate,
)

TOOLS = {t.name: t for t in (*STRUCTURED_TOOLS, *BASH_ONLY_TOOLS)}


def call(sandbox: Sandbox, name: str, inp: object, max_chars: int = 10_000):  # type: ignore[no-untyped-def]
    return execute_tool(TOOLS.get(name), sandbox, inp, max_chars)


def test_tool_sets() -> None:
    assert [t.name for t in get_tools("structured")] == [
        "list_files",
        "read_file",
        "str_replace_edit",
        "create_file",
        "run_tests",
    ]
    assert [t.name for t in get_tools("bash_only")] == ["bash"]
    for p in to_api_tool_params(get_tools("structured")):
        assert set(p) == {"name", "description", "input_schema"}


def test_str_replace_zero_match(sandbox: Sandbox) -> None:
    sandbox.write_text("f.py", "a = 1\n")
    out = call(sandbox, "str_replace_edit", {"path": "f.py", "old_str": "zzz", "new_str": "y"})
    assert out.is_error and "not found" in out.content
    assert sandbox.read_text("f.py") == "a = 1\n"


def test_str_replace_multi_match(sandbox: Sandbox) -> None:
    sandbox.write_text("f.py", "x = 1\nx = 1\n")
    out = call(sandbox, "str_replace_edit", {"path": "f.py", "old_str": "x = 1", "new_str": "y"})
    assert out.is_error and "2 times" in out.content
    assert sandbox.read_text("f.py") == "x = 1\nx = 1\n"


def test_str_replace_single_match_and_empty(sandbox: Sandbox) -> None:
    sandbox.write_text("f.py", "x = 1\ny = 2\n")
    ok = call(sandbox, "str_replace_edit", {"path": "f.py", "old_str": "y = 2", "new_str": "y = 3"})
    assert not ok.is_error and sandbox.read_text("f.py") == "x = 1\ny = 3\n"
    empty = call(sandbox, "str_replace_edit", {"path": "f.py", "old_str": "", "new_str": "q"})
    assert empty.is_error


def test_read_file_line_ranges(sandbox: Sandbox) -> None:
    sandbox.write_text("f.txt", "one\ntwo\nthree\nfour\n")
    out = call(sandbox, "read_file", {"path": "f.txt", "start_line": 2, "end_line": 3})
    assert "two" in out.content and "three" in out.content
    assert "one" not in out.content and "four" not in out.content
    whole = call(sandbox, "read_file", {"path": "f.txt"})
    assert "four" in whole.content and "one" in whole.content
    assert call(sandbox, "read_file", {"path": "f.txt", "start_line": 0}).is_error


def test_create_file_and_list(sandbox: Sandbox) -> None:
    assert "Created" in call(sandbox, "create_file", {"path": "p/a.py", "content": "x"}).content
    assert "Overwrote" in call(sandbox, "create_file", {"path": "p/a.py", "content": "y"}).content
    assert call(sandbox, "list_files", {}).content == "p/a.py"


def test_failures_never_raise(sandbox: Sandbox) -> None:
    assert call(sandbox, "read_file", {"path": "missing.txt"}).is_error
    assert call(sandbox, "read_file", {"path": 5}).is_error
    assert call(sandbox, "read_file", "not a dict").is_error
    assert call(sandbox, "nope", {}).is_error
    assert call(sandbox, "create_file", {"path": "/etc/x", "content": "c"}).is_error

    def boom(sb: Sandbox, inp: dict[str, object]) -> str:
        raise RuntimeError("kaboom")

    out = execute_tool(Tool("boom", "", {}, boom), sandbox, {}, 100)
    assert out.is_error and "kaboom" in out.content


def test_truncate_marker() -> None:
    assert truncate("short", 10) == "short"
    out = truncate("x" * 100, 10)
    assert out.startswith("x" * 10) and "truncated: 90 more chars" in out


def test_bash_tool_and_timeout(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    out = call(sandbox, "bash", {"command": "echo hello"})
    assert "hello" in out.content and "[exit code: 0]" in out.content and not out.is_error
    monkeypatch.setattr("agent_lab.harness.tools.DEFAULT_COMMAND_TIMEOUT_S", 1)
    slow = call(sandbox, "bash", {"command": "sleep 30"})
    assert slow.is_error and "timed out" in slow.content


def test_run_tests(sandbox: Sandbox) -> None:
    sandbox.write_text("test_ok.py", "def test_ok():\n    assert True\n")
    assert "1 passed" in call(sandbox, "run_tests", {}).content
    sandbox.write_text("test_bad.py", "def test_bad():\n    assert 1 == 2\n")
    bad = call(sandbox, "run_tests", {})
    assert "failed" in bad.content and "[exit code: 1]" in bad.content


def test_redaction_of_file_contents(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_lab.harness.redact._API_KEY", "plain-secret-without-prefix")
    sandbox.write_text("env.txt", "KEY=plain-secret-without-prefix\nOTHER=sk-ant-api03-AbC_d-1\n")
    out = call(sandbox, "bash", {"command": "cat env.txt"})
    assert "plain-secret" not in out.content and "sk-ant" not in out.content
    assert out.content.count("[REDACTED]") == 2
