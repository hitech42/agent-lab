from typing import Any

import pytest
from conftest import FakeClient, status_error, text_response, tool_use_response, usage

from agent_lab.harness.agent import Agent
from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.model_client import ModelClient, RetryPolicy
from agent_lab.harness.pricing import ModelPrice
from agent_lab.harness.redact import Redactor
from agent_lab.harness.sandbox import Sandbox

PRICE = ModelPrice(input=1.0, output=2.0, cache_write=3.0, cache_read=0.5)


def run(
    config: AgentConfig,
    sandbox: Sandbox,
    script: list[Any],
    budget: Budget | None = None,
    sleeps: list[float] | None = None,
    redactor: Redactor | None = None,
) -> tuple[Any, FakeClient]:
    client = FakeClient(script)
    policy = RetryPolicy(sleep=(sleeps if sleeps is not None else []).append)
    agent = Agent.from_config(config, ModelClient(client, policy), redactor or Redactor([]))
    return agent.run("do the task", sandbox, budget or Budget()), client


def test_multi_turn_tool_loop_to_end_turn(config: AgentConfig, sandbox: Sandbox) -> None:
    script = [
        tool_use_response(("t1", "create_file", {"path": "a.py", "content": "print(1)\n"})),
        tool_use_response(("t2", "read_file", {"path": "a.py"}), text="checking"),
        text_response("all done", usage(7, 3, cw=2, cr=4)),
    ]
    result, client = run(config, sandbox, script)

    assert result.status == "completed"
    assert result.final_text == "all done"
    assert (sandbox.root / "a.py").read_text() == "print(1)\n"
    assert [t.turn for t in result.trace.turns] == [1, 2, 3]
    assert result.trace.total_input_tokens == 27
    assert result.trace.total_output_tokens == 13
    assert result.trace.total_cache_creation_input_tokens == 2
    assert result.trace.total_cache_read_input_tokens == 4
    assert result.trace.tool_call_count == 2
    roles = [m["role"] for m in result.messages]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
    # each request carries the growing history
    assert [len(r["messages"]) for r in client.requests] == [1, 3, 5]
    # assistant blocks are preserved losslessly (tool_use keeps id/name/input)
    assistant = result.messages[1]["content"][0]
    assert assistant["type"] == "tool_use" and assistant["id"] == "t1"
    assert assistant["input"] == {"path": "a.py", "content": "print(1)\n"}


def test_multiple_tool_use_blocks_return_one_user_message(
    config: AgentConfig, sandbox: Sandbox
) -> None:
    script = [
        tool_use_response(
            ("a", "create_file", {"path": "x.txt", "content": "x"}),
            ("b", "create_file", {"path": "y.txt", "content": "y"}),
            ("c", "list_files", {}),
        ),
        text_response(),
    ]
    result, client = run(config, sandbox, script)

    second_request_messages = client.requests[1]["messages"]
    assert len(second_request_messages) == 3  # user, assistant, ONE user with all results
    results = second_request_messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b", "c"]
    assert all(r["type"] == "tool_result" for r in results)
    assert "x.txt" in results[2]["content"] and "y.txt" in results[2]["content"]
    assert result.trace.turns[0].tool_calls[0].name == "create_file"


def test_tool_error_returned_as_is_error_result(config: AgentConfig, sandbox: Sandbox) -> None:
    script = [
        tool_use_response(("e1", "read_file", {"path": "../escape.txt"})),
        tool_use_response(("e2", "nonexistent_tool", {})),
        text_response(),
    ]
    result, client = run(config, sandbox, script)

    assert result.status == "completed"
    r1 = client.requests[1]["messages"][-1]["content"][0]
    r2 = client.requests[2]["messages"][-1]["content"][0]
    assert r1["is_error"] is True and "outside the sandbox" in r1["content"]
    assert r2["is_error"] is True and "unknown tool" in r2["content"]
    assert result.trace.tool_error_count == 2


def test_successful_tool_result_is_not_error(config: AgentConfig, sandbox: Sandbox) -> None:
    script = [tool_use_response(("ok", "list_files", {})), text_response()]
    _, client = run(config, sandbox, script)
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is False


def test_max_turns_stop(config: AgentConfig, sandbox: Sandbox) -> None:
    cfg = config.model_copy(update={"max_turns": 2})
    script = [tool_use_response((f"t{i}", "list_files", {})) for i in range(5)]
    result, client = run(cfg, sandbox, script)

    assert result.status == "max_turns"
    assert len(client.requests) == 2
    assert len(result.trace.turns) == 2


def test_budget_stop_on_tokens(config: AgentConfig, sandbox: Sandbox) -> None:
    script = [
        tool_use_response(("t1", "list_files", {}), u=usage(50, 50)),
        tool_use_response(("t2", "create_file", {"path": "z", "content": "z"}), u=usage(50, 50)),
        text_response(),
    ]
    result, client = run(config, sandbox, script, Budget(max_total_tokens=150))

    assert result.status == "budget_exceeded"
    assert len(client.requests) == 2
    # tools from the over-budget response are not executed
    assert not (sandbox.root / "z").exists()


def test_budget_stop_on_cost(config: AgentConfig, sandbox: Sandbox) -> None:
    script = [tool_use_response(("t1", "list_files", {}), u=usage(1_000_000, 0)), text_response()]
    budget = Budget(max_cost_usd=0.5, price=PRICE)
    result, _ = run(config, sandbox, script, budget)

    assert result.status == "budget_exceeded"
    assert result.cost_usd == pytest.approx(1.0)


def test_cost_reported_with_price(config: AgentConfig, sandbox: Sandbox) -> None:
    result, _ = run(
        config, sandbox, [text_response(u=usage(1_000_000, 1_000_000))], Budget(price=PRICE)
    )
    assert result.status == "completed"
    assert result.cost_usd == pytest.approx(3.0)


def test_cache_control_on_system_and_last_tool_only(config: AgentConfig, sandbox: Sandbox) -> None:
    _, client = run(config, sandbox, [text_response()])
    req = client.requests[0]
    assert req["system"] == [
        {"type": "text", "text": "You are a test agent.", "cache_control": {"type": "ephemeral"}}
    ]
    flags = ["cache_control" in t for t in req["tools"]]
    assert flags == [False] * (len(flags) - 1) + [True]
    assert req["model"] == "claude-haiku-5-5" and req["max_tokens"] == 1000


def test_retry_then_success_records_retries(config: AgentConfig, sandbox: Sandbox) -> None:
    sleeps: list[float] = []
    script = [status_error(429), status_error(529), text_response()]
    result, client = run(config, sandbox, script, sleeps=sleeps)

    assert result.status == "completed"
    assert result.trace.turns[0].retries == 2
    assert sleeps == [1.0, 2.0]
    assert len(client.requests) == 3


def test_retries_exhausted_recorded_not_raised(config: AgentConfig, sandbox: Sandbox) -> None:
    script = [status_error(500), status_error(500), status_error(500), text_response()]
    result, client = run(config, sandbox, script)

    assert result.status == "error"
    assert result.error is not None and "APIStatusError" in result.error
    assert len(client.requests) == 3  # max 3 attempts


def test_non_transient_error_not_retried(config: AgentConfig, sandbox: Sandbox) -> None:
    result, client = run(config, sandbox, [status_error(400), text_response()])

    assert result.status == "error"
    assert len(client.requests) == 1


def test_unimplemented_context_strategy_raises(config: AgentConfig) -> None:
    cfg = config.model_copy(update={"context_strategy": "summarize"})
    client = FakeClient([text_response()])
    with pytest.raises(NotImplementedError):  # fails at construction, before any run
        Agent.from_config(cfg, ModelClient(client, RetryPolicy()), Redactor([]))
    assert client.requests == []


def test_tool_output_is_truncated_and_redacted(config: AgentConfig, sandbox: Sandbox) -> None:
    cfg = config.model_copy(update={"tool_output_max_chars": 50})
    sandbox.write_text("k.txt", "super-secret-value-123 sk-ant-abc_DEF-9 " + "x" * 500)
    script = [tool_use_response(("r", "read_file", {"path": "k.txt"})), text_response()]
    result, client = run(cfg, sandbox, script, redactor=Redactor(["super-secret-value-123"]))

    content = client.requests[1]["messages"][-1]["content"][0]["content"]
    assert "super-secret" not in content and "sk-ant" not in content
    assert "[REDACTED]" in content and "truncated" in content
    assert "super-secret" not in str(result.messages)


def test_tool_result_sent_to_model_is_redacted(config: AgentConfig, sandbox: Sandbox) -> None:
    """A tool reads a file with secrets; the next request carries only redacted content."""
    sandbox.write_text("creds.txt", "a=plain-env-secret-value\nb=sk-ant-api03-FAKE_key-1\n")
    script = [tool_use_response(("r", "read_file", {"path": "creds.txt"})), text_response()]
    result, client = run(config, sandbox, script, redactor=Redactor(["plain-env-secret-value"]))

    sent = client.requests[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and sent["content"].count("[REDACTED]") == 2
    assert "plain-env-secret-value" not in str(client.requests[1])
    assert "sk-ant-api03" not in str(client.requests[1])
    assert "plain-env-secret-value" not in str(result.messages)


def test_unexpected_exception_from_client_is_recorded(
    config: AgentConfig, sandbox: Sandbox
) -> None:
    script = [tool_use_response(("t1", "list_files", {})), ValueError("bad sk-ant-leak_123 value")]
    result, _ = run(config, sandbox, script)

    assert result.status == "error"
    assert result.error == "ValueError: bad [REDACTED] value"
    assert len(result.trace.turns) == 1  # turns collected so far are kept
    assert result.messages[-1]["role"] == "user"


def test_latency_split_between_call_and_total(config: AgentConfig, sandbox: Sandbox) -> None:
    sleeps: list[float] = []
    result, _ = run(config, sandbox, [status_error(429), text_response()], sleeps=sleeps)

    turn = result.trace.turns[0]
    assert turn.retries == 1
    assert 0 <= turn.latency_s <= turn.total_latency_s
    assert result.trace.total_latency_s == pytest.approx(turn.total_latency_s)


def test_total_latency_includes_backoff_sleep(config: AgentConfig, sandbox: Sandbox) -> None:
    import time as _time

    client = FakeClient([status_error(503), text_response()])
    policy = RetryPolicy(sleep=lambda s: _time.sleep(0.2))
    agent = Agent.from_config(config, ModelClient(client, policy), Redactor([]))
    result = agent.run("x", sandbox, Budget())

    turn = result.trace.turns[0]
    assert turn.total_latency_s >= 0.2  # the backoff sleep counts
    assert turn.latency_s < 0.2  # the successful call alone does not
