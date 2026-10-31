import pytest

from agent_lab.harness.sandbox import Sandbox
from agent_lab.harness.tools import BashTool, RunTestsTool, ToolError


def test_bash_runs_command(sandbox: Sandbox) -> None:
    out = BashTool().execute(sandbox, {"command": "echo hello"})
    assert "hello" in out and out.endswith("[exit code: 0]")


def test_bash_requires_command(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="'command' is required"):
        BashTool().execute(sandbox, {})


def test_bash_timeout_is_an_error(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="timed out"):
        BashTool(timeout_s=1).execute(sandbox, {"command": "sleep 30"})


@pytest.mark.slow
def test_run_tests_pass_and_fail(sandbox: Sandbox) -> None:
    sandbox.write_text("test_ok.py", "def test_ok():\n    assert True\n")
    assert "1 passed" in RunTestsTool().execute(sandbox, {})
    sandbox.write_text("test_bad.py", "def test_bad():\n    assert 1 == 2\n")
    bad = RunTestsTool().execute(sandbox, {})
    assert "failed" in bad and "[exit code: 1]" in bad


@pytest.mark.slow
def test_run_tests_output_is_tail_trimmed(sandbox: Sandbox) -> None:
    sandbox.write_text("test_big.py", "def test_big():\n    assert 0, 'x' * 20000\n")
    out = RunTestsTool().execute(sandbox, {})
    assert "earlier chars trimmed" in out
    assert out.rstrip().endswith("[exit code: 1]")


@pytest.mark.slow
def test_run_tests_timeout_is_an_error(sandbox: Sandbox) -> None:
    sandbox.write_text("test_slow.py", "import time\n\ndef test_slow():\n    time.sleep(30)\n")
    with pytest.raises(ToolError, match="timed out"):
        RunTestsTool(timeout_s=1).execute(sandbox, {})


def test_bash_description_states_the_real_timeout() -> None:
    default = "Run a shell command in the working directory (60s timeout)."
    assert BashTool().description == default  # byte-identical to the original text
    assert BashTool().to_api_param()["description"] == default
    assert BashTool(timeout_s=5).description.endswith("(5s timeout).")
    assert "0.5s timeout" in BashTool(timeout_s=0.5).description
