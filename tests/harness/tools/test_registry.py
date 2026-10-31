from typing import Any, ClassVar

import pytest

from agent_lab.harness.redact import Redactor
from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools import Tool, ToolRegistry


class BoomTool(Tool):
    name = "boom"
    description = "always fails"
    input_schema: ClassVar[dict[str, Any]] = Tool._schema({}, [])

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        raise RuntimeError("kaboom")


class EchoTool(Tool):
    name = "echo"
    description = "echoes text"
    input_schema: ClassVar[dict[str, Any]] = Tool._schema({"text": {"type": "string"}}, ["text"])

    def execute(self, sandbox: Sandbox, input: dict[str, Any]) -> str:
        return self._req_str(input, "text")


def registry(max_chars: int = 10_000, secrets: list[str] | None = None) -> ToolRegistry:
    return ToolRegistry([BoomTool(), EchoTool()], Redactor(secrets or []), max_chars)


def test_tool_sets_contents_and_order() -> None:
    structured = ToolRegistry.for_set("structured", Redactor([]), 100).api_params()
    assert [p["name"] for p in structured] == [
        "list_files",
        "read_file",
        "str_replace_edit",
        "create_file",
        "run_tests",
    ]
    bash_only = ToolRegistry.for_set("bash_only", Redactor([]), 100).api_params()
    assert [p["name"] for p in bash_only] == ["bash"]


def test_api_params_cache_control_on_last_tool_only() -> None:
    params = ToolRegistry.for_set("structured", Redactor([]), 100).api_params()
    assert ["cache_control" in p for p in params] == [False] * 4 + [True]
    assert params[-1]["cache_control"] == {"type": "ephemeral"}
    assert set(params[0]) == {"name", "description", "input_schema"}
    single = ToolRegistry.for_set("bash_only", Redactor([]), 100).api_params()
    assert single[0]["cache_control"] == {"type": "ephemeral"}


def test_api_params_returns_fresh_copies() -> None:
    reg = ToolRegistry.for_set("structured", Redactor([]), 100)
    reg.api_params()[1]["input_schema"]["properties"]["path"]["injected"] = True
    assert "injected" not in reg.api_params()[1]["input_schema"]["properties"]["path"]


def test_execute_success(sandbox: Sandbox) -> None:
    out = registry().execute("echo", {"text": "hi"}, sandbox)
    assert (out.content, out.is_error) == ("hi", False)


@pytest.mark.parametrize(
    ("name", "inp", "expected"),
    [
        ("nope", {}, "unknown tool"),
        ("echo", "not a dict", "must be a JSON object"),
        ("echo", {}, "'text' is required"),
        ("boom", {}, "RuntimeError: kaboom"),
    ],
)
def test_execute_failures_never_raise(
    sandbox: Sandbox, name: str, inp: object, expected: str
) -> None:
    out = registry().execute(name, inp, sandbox)
    assert out.is_error and out.content.startswith("Error: ") and expected in out.content


def test_execute_truncates_with_visible_marker(sandbox: Sandbox) -> None:
    assert registry(10).execute("echo", {"text": "short"}, sandbox).content == "short"
    out = registry(10).execute("echo", {"text": "x" * 100}, sandbox)
    assert out.content.startswith("x" * 10) and "truncated: 90 more chars" in out.content


def test_execute_redacts_then_truncates(sandbox: Sandbox) -> None:
    secret = "plain-secret-without-prefix"
    reg = registry(max_chars=15, secrets=[secret])
    out = reg.execute("echo", {"text": f"KEY={secret} sk-ant-api03-AbC_d-1"}, sandbox)
    assert secret not in out.content and "sk-ant" not in out.content
    assert out.content.startswith("KEY=[REDACTED] ")  # redaction applied before the cut


def test_redaction_of_file_contents_via_bash(sandbox: Sandbox) -> None:
    secret = "plain-secret-without-prefix"
    sandbox.write_text("env.txt", f"KEY={secret}\nOTHER=sk-ant-api03-AbC_d-1\n")
    reg = ToolRegistry.for_set("bash_only", Redactor([secret]), 10_000)
    out = reg.execute("bash", {"command": "cat env.txt"}, sandbox)
    assert secret not in out.content and "sk-ant" not in out.content
    assert out.content.count("[REDACTED]") == 2
