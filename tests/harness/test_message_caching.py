"""Message caching: one cache_control marker on the last block of the last message of a request.

Caching must change cost, not behavior: with the markers removed the requests are identical to
the ones sent without caching. Nothing here touches the network.
"""

import copy
import json
from typing import Any

import pytest
from conftest import FakeClient, text_response, tool_use_response, usage

from agent_lab.harness.agent import Agent
from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.model_client import ModelClient, RetryPolicy
from agent_lab.harness.redact import Redactor
from agent_lab.harness.sandbox import Sandbox

CACHE = {"type": "ephemeral"}
BODY = "x" * 300  # long enough that an elided result is shorter than the original


def configured(config: AgentConfig, **changes: Any) -> AgentConfig:
    return AgentConfig.model_validate({**config.model_dump(), **changes})


def tool_loop(turns: int = 4) -> list[Any]:
    """A create_file, then ``turns - 1`` read_file calls, then a final answer."""
    script: list[Any] = [
        tool_use_response(("t1", "create_file", {"path": "a.py", "content": BODY}), text="go")
    ]
    script += [
        tool_use_response((f"t{n}", "read_file", {"path": "a.py"}), text=f"step {n}")
        for n in range(2, turns + 1)
    ]
    script.append(text_response("done", usage()))
    return script


def run(config: AgentConfig, sandbox: Sandbox, script: list[Any]) -> tuple[Any, FakeClient, Agent]:
    client = FakeClient(script)
    agent = Agent.from_config(
        config, ModelClient(client, RetryPolicy(sleep=lambda _: None)), Redactor([])
    )
    return agent.run("do the task", sandbox, Budget()), client, agent


def marked_blocks(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(message index, block index) of every block that carries cache_control."""
    return [
        (m, b)
        for m, message in enumerate(messages)
        if isinstance(message["content"], list)
        for b, block in enumerate(message["content"])
        if "cache_control" in block
    ]


def without_markers(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages with cache_control removed and string content as one text block."""
    out = copy.deepcopy(messages)
    for message in out:
        if isinstance(message["content"], str):
            message["content"] = [{"type": "text", "text": message["content"]}]
        for block in message["content"]:
            block.pop("cache_control", None)
    return out


# --- request shape --------------------------------------------------------------------------


@pytest.mark.parametrize("setting", [None, False])
def test_caching_is_off_unless_it_is_true(
    config: AgentConfig, sandbox: Sandbox, setting: bool | None
) -> None:
    _, client, _ = run(configured(config, cache_messages=setting), sandbox, tool_loop())
    for request in client.requests:
        assert marked_blocks(request["messages"]) == []
        assert json.dumps(request).count('"cache_control"') == 2  # system and the last tool only


def test_the_marker_is_on_the_last_block_of_the_last_message_of_every_request(
    config: AgentConfig, sandbox: Sandbox
) -> None:
    _, client, _ = run(configured(config, cache_messages=True), sandbox, tool_loop())
    assert len(client.requests) == 5
    for request in client.requests:
        messages = request["messages"]
        assert marked_blocks(messages) == [(len(messages) - 1, len(messages[-1]["content"]) - 1)]
        assert messages[-1]["role"] == "user"
    first = client.requests[0]["messages"][0]  # a plain-string prompt becomes one text block
    assert first["content"] == [{"type": "text", "text": "do the task", "cache_control": CACHE}]
    later = client.requests[3]["messages"][-1]["content"][-1]
    assert later["type"] == "tool_result" and later["cache_control"] == CACHE


@pytest.mark.parametrize("tool_set", ["structured", "bash_only"])
def test_three_breakpoints_at_most_and_the_api_allows_four(
    config: AgentConfig, sandbox: Sandbox, tool_set: str
) -> None:
    cfg = configured(config, cache_messages=True, tool_set=tool_set)
    script: list[Any] = [
        tool_use_response(("t1", "bash", {"command": "echo hi"}))
        if tool_set == "bash_only"
        else tool_use_response(("t1", "list_files", {})),
        text_response("done"),
    ]
    _, client, _ = run(cfg, sandbox, script)
    for request in client.requests:
        assert json.dumps(request).count('"cache_control"') == 3  # tool, system, last message


def test_the_conversation_and_the_transcript_never_contain_markers(
    config: AgentConfig, sandbox: Sandbox
) -> None:
    result, _, _ = run(configured(config, cache_messages=True), sandbox, tool_loop())
    assert "cache_control" not in json.dumps(result.messages)
    assert result.messages[0] == {"role": "user", "content": "do the task"}


def test_with_the_markers_removed_the_requests_equal_the_uncached_ones(
    config: AgentConfig, sandbox: Sandbox, tmp_path: Any
) -> None:
    _, plain, _ = run(config, sandbox, tool_loop())
    with Sandbox(tmp_path / "second") as other:
        _, cached, _ = run(configured(config, cache_messages=True), other, tool_loop())
    assert len(plain.requests) == len(cached.requests)
    for off, on in zip(plain.requests, cached.requests, strict=True):
        assert without_markers(on["messages"]) == without_markers(off["messages"])
        assert {k: v for k, v in on.items() if k != "messages"} == {
            k: v for k, v in off.items() if k != "messages"
        }  # model, effort, system, tools, max_tokens: all unchanged


def test_the_warmup_request_is_unchanged(config: AgentConfig, sandbox: Sandbox) -> None:
    _, _, agent = run(configured(config, cache_messages=True), sandbox, [text_response()])
    warm = agent.warmup_request()
    assert warm.messages == [{"role": "user", "content": "ok"}]
    assert warm.max_tokens == 1 and warm.system[0]["cache_control"] == CACHE


# --- the helper on its own ------------------------------------------------------------------


def test_the_helper_does_not_mutate_or_alias_what_it_is_given() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "r"}]},
    ]
    before = copy.deepcopy(messages)
    marked = Agent._with_message_cache(messages)
    assert messages == before  # the input is untouched, markers included
    assert marked is not messages and marked[-1] is not messages[-1]
    assert marked[-1]["content"] is not messages[-1]["content"]
    assert marked[-1]["content"][-1] == {**before[-1]["content"][-1], "cache_control": CACHE}
    assert marked_blocks(marked) == [(2, 0)]


@pytest.mark.parametrize(
    "last",
    [
        {"type": "thinking", "thinking": "hmm", "signature": "s"},
        {"type": "redacted_thinking", "data": "x"},
        {"type": "text", "text": ""},
    ],
)
def test_a_last_block_that_cannot_carry_the_marker_is_left_alone(last: dict[str, Any]) -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": [last]},
    ]
    assert Agent._with_message_cache(messages) == messages  # unmarked, no error


def test_the_helper_handles_no_messages_and_empty_content() -> None:
    assert Agent._with_message_cache([]) == []
    empty: list[dict[str, Any]] = [{"role": "user", "content": []}]
    assert Agent._with_message_cache(empty) == empty


# --- composition with the context strategies ------------------------------------------------


def test_with_truncation_the_marker_is_on_the_last_block_after_eliding(
    config: AgentConfig, sandbox: Sandbox
) -> None:
    cfg = configured(
        config,
        cache_messages=True,
        context_strategy="truncate_tool_outputs",
        context_keep_last=2,
    )
    _, client, _ = run(cfg, sandbox, tool_loop(4))
    messages = client.requests[-1]["messages"]  # five calls: results of turns 1 and 2 are old
    results = [
        block
        for m in messages
        if m["role"] == "user" and isinstance(m["content"], list)
        for block in m["content"]
        if block["type"] == "tool_result"
    ]
    # turn 1's create_file result is shorter than the placeholder, so it is kept as it is
    assert [r["content"].startswith("[tool output elided") for r in results] == [
        False,
        True,
        False,
        False,
    ]
    assert marked_blocks(messages) == [(len(messages) - 1, 0)]
    assert results[-1]["cache_control"] == CACHE and "cache_control" not in results[0]


def test_with_summarize_the_summary_is_unmarked_and_the_last_block_is_marked(
    config: AgentConfig, sandbox: Sandbox
) -> None:
    cfg = configured(
        config,
        cache_messages=True,
        context_strategy="summarize",
        context_keep_last=2,
        context_after_turns=3,
        context_summary_max_tokens=200,
    )
    script = tool_loop(4)
    script.insert(4, text_response("worked on a.py", usage()))  # the summary call, before call 5
    _, client, _ = run(cfg, sandbox, script)
    summary_request, final = client.requests[4], client.requests[5]
    assert "tools" not in summary_request  # context calls are never cached
    assert "cache_control" not in json.dumps(summary_request)
    first = final["messages"][0]["content"]
    assert [b["type"] for b in first] == ["text", "text"]  # prompt + progress summary
    assert "cache_control" not in first[0] and "cache_control" not in first[1]
    last = final["messages"][-1]
    assert marked_blocks(final["messages"]) == [
        (len(final["messages"]) - 1, len(last["content"]) - 1)
    ]
