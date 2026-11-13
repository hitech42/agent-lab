import itertools
from typing import Any, get_args

import pytest
from conftest import FakeClient, text_response, usage

from agent_lab.harness.config import AgentConfig, ContextStrategyName
from agent_lab.harness.context import (
    _REGISTRY,
    ContextStrategy,
    FullHistory,
    Summarize,
    TruncateToolOutputs,
)
from agent_lab.harness.conversation import Conversation
from agent_lab.harness.model_client import ModelClient, RetryPolicy

PROMPT = "fix the bug in app.py"


def tool_turn(
    conv: Conversation,
    n: int,
    *calls: tuple[str, str],
    thinking: bool = False,
    size: int = 300,
) -> None:
    """One assistant turn with tool calls (name, result text per call) and its results."""
    blocks: list[dict[str, Any]] = []
    if thinking:
        blocks.append({"type": "thinking", "thinking": f"hmm {n}", "signature": f"sig{n}"})
    blocks.append({"type": "text", "text": f"step {n}"})
    for k, (name, _) in enumerate(calls):
        blocks.append({"type": "tool_use", "id": f"t{n}_{k}", "name": name, "input": {"n": n}})
    conv.add_assistant(blocks)
    conv.add_tool_results(
        [
            {
                "type": "tool_result",
                "tool_use_id": f"t{n}_{k}",
                "content": (text * size)[:size],
                "is_error": False,
            }
            for k, (_, text) in enumerate(calls)
        ]
    )


def conversation(turns: int, parallel: bool = False, thinking: bool = False) -> Conversation:
    conv = Conversation(PROMPT)
    for n in range(1, turns + 1):
        calls = [("read_file", "R"), ("list_files", "L")] if parallel else [("read_file", f"{n}")]
        tool_turn(conv, n, *calls, thinking=thinking)
    return conv


def summarizer(*summaries: str) -> tuple[ModelClient, FakeClient]:
    fake = FakeClient([text_response(s, usage(100, 20)) for s in summaries])
    return ModelClient(fake, RetryPolicy()), fake


def make(name: str, client: ModelClient) -> ContextStrategy:
    cfg: dict[str, Any] = {
        "full": {},
        "truncate_tool_outputs": {"context_keep_last": 2},
        "summarize": {
            "context_keep_last": 2,
            "context_after_turns": 3,
            "context_summary_max_tokens": 200,
        },
    }[name]
    config = AgentConfig(
        name="t",
        model="claude-haiku-5-5",
        system_prompt="s",
        effort="low",
        tool_set="structured",
        max_turns=15,
        max_tokens_per_call=1000,
        tool_output_max_chars=2000,
        context_strategy=name,  # type: ignore[arg-type]
        **cfg,
    )
    return ContextStrategy.from_config(config, client)


# --- the contract, for every registered strategy ------------------------------------------


def assert_valid_sequence(messages: list[dict[str, Any]]) -> None:
    assert messages[0]["role"] == "user"
    first = messages[0]["content"]
    first_text = first if isinstance(first, str) else first[0]["text"]
    assert PROMPT in first_text
    assert [m["role"] for m in messages] == [
        ("user", "assistant")[i % 2] for i in range(len(messages))
    ]
    for i, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        used = [b["id"] for b in message["content"] if b["type"] == "tool_use"]
        if not used:
            continue
        reply = messages[i + 1]  # immediately following, always present in these conversations
        assert reply["role"] == "user"
        answered = [b["tool_use_id"] for b in reply["content"] if b["type"] == "tool_result"]
        assert sorted(answered) == sorted(used)
    ids = {
        b["id"]
        for m in messages
        if m["role"] == "assistant"
        for b in m["content"]
        if b["type"] == "tool_use"
    }
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b["type"] == "tool_result":
                    assert b["tool_use_id"] in ids


def test_every_strategy_name_is_registered_and_nothing_else() -> None:
    assert set(_REGISTRY) == set(get_args(ContextStrategyName))


@pytest.mark.parametrize("name", sorted(_REGISTRY))
@pytest.mark.parametrize("turns", [1, 2, 3, 4, 6, 9])
@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("thinking", [False, True])
def test_contract_holds_for_every_strategy(
    name: str, turns: int, parallel: bool, thinking: bool
) -> None:
    client, _ = summarizer(*(f"summary {i}" for i in range(10)))
    strategy = make(name, client).fresh()
    # replay the loop: apply on every prefix, as the agent would on each model call
    conv = Conversation(PROMPT)
    for n in range(1, turns + 1):
        calls = [("read_file", "R"), ("list_files", "L")] if parallel else [("read_file", f"{n}")]
        tool_turn(conv, n, *calls, thinking=thinking)
        before = conv.messages
        result = strategy.apply(conv)
        assert conv.messages == before  # never mutates the conversation
        assert_valid_sequence(result.messages)
        assert result.messages[-1]["role"] == "user"
        assert result.messages[-1] == before[-1] or name != "full"
    assert_valid_sequence(strategy.apply(conv).messages)


# --- FullHistory ----------------------------------------------------------------------------


def test_full_history_returns_everything_unchanged() -> None:
    conv = conversation(3)
    result = FullHistory().apply(conv)
    assert result.messages == conv.messages and result.calls == ()


def test_stateless_strategies_return_themselves_from_fresh() -> None:
    for strategy in (FullHistory(), TruncateToolOutputs(2)):
        assert strategy.fresh() is strategy


# --- TruncateToolOutputs ---------------------------------------------------------------------


def results_of(messages: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [b["content"] for b in m["content"]]
        for m in messages
        if m["role"] == "user" and isinstance(m["content"], list)
    ]


def elided(text: str) -> bool:
    return text.startswith("[tool output elided")


def test_results_older_than_the_last_keep_last_turns_are_elided() -> None:
    conv = conversation(5, parallel=True)
    out = TruncateToolOutputs(2).apply(conv).messages
    flags = [[elided(t) for t in turn] for turn in results_of(out)]
    assert flags == [[True, True]] * 3 + [[False, False]] * 2


def test_nothing_is_elided_until_there_are_more_than_keep_last_turns() -> None:
    for turns in (1, 2):
        conv = conversation(turns)
        assert TruncateToolOutputs(2).apply(conv).messages == conv.messages


def test_placeholder_names_the_size_and_the_tool() -> None:
    conv = Conversation(PROMPT)
    tool_turn(conv, 1, ("read_file", "x"), ("run_tests", "y"), size=1234)
    tool_turn(conv, 2, ("read_file", "z"))
    out = TruncateToolOutputs(1).apply(conv).messages
    first = results_of(out)[0]
    assert first == [
        "[tool output elided: 1234 characters from read_file]",
        "[tool output elided: 1234 characters from run_tests]",
    ]


def test_everything_else_is_unchanged() -> None:
    conv = conversation(5, thinking=True)
    original = conv.messages
    out = TruncateToolOutputs(2).apply(conv).messages
    assert [m for m in out if m["role"] == "assistant"] == [
        m for m in original if m["role"] == "assistant"
    ]
    assert out[0] == original[0]
    assert out[-2:] == original[-2:]  # the last turn and its results
    for old, new in zip(original, out, strict=True):
        if old["role"] == "user" and isinstance(old["content"], list):
            for a, b in zip(old["content"], new["content"], strict=True):
                assert (a["tool_use_id"], a["is_error"]) == (b["tool_use_id"], b["is_error"])


def test_error_flag_survives_elision() -> None:
    conv = Conversation(PROMPT)
    conv.add_assistant([{"type": "tool_use", "id": "a", "name": "bash", "input": {}}])
    conv.add_tool_results(
        [{"type": "tool_result", "tool_use_id": "a", "content": "E" * 200, "is_error": True}]
    )
    tool_turn(conv, 2, ("bash", "ok"))
    block = TruncateToolOutputs(1).apply(conv).messages[2]["content"][0]
    assert elided(block["content"]) and block["is_error"] is True


def test_short_results_and_list_content_are_handled() -> None:
    conv = Conversation(PROMPT)
    conv.add_assistant([{"type": "tool_use", "id": "a", "name": "bash", "input": {}}])
    conv.add_tool_results(
        [{"type": "tool_result", "tool_use_id": "a", "content": "ok", "is_error": False}]
    )
    conv.add_assistant([{"type": "tool_use", "id": "b", "name": "bash", "input": {}}])
    conv.add_tool_results(
        [
            {
                "type": "tool_result",
                "tool_use_id": "b",
                "content": [{"type": "text", "text": "L" * 500}],
                "is_error": False,
            }
        ]
    )
    tool_turn(conv, 3, ("bash", "x"))
    out = TruncateToolOutputs(1).apply(conv).messages
    assert out[2]["content"][0]["content"] == "ok"  # shorter than its placeholder: kept
    assert out[4]["content"][0]["content"] == "[tool output elided: 500 characters from bash]"


def test_elided_prefix_is_stable_from_one_turn_to_the_next() -> None:
    strategy = TruncateToolOutputs(2)
    conv = Conversation(PROMPT)
    previous: list[dict[str, Any]] = []
    for n in range(1, 8):
        tool_turn(conv, n, ("read_file", "r"))
        current = strategy.apply(conv).messages
        stable = len(previous) - 3  # only the results that just aged out of the window change
        assert current[:stable] == previous[:stable]
        previous = current
    assert strategy.apply(conv).messages == strategy.apply(conv).messages


def test_keep_last_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TruncateToolOutputs(0)


# --- Summarize -------------------------------------------------------------------------------


def summarize(client: ModelClient, after: int = 3, keep: int = 2) -> Summarize:
    return Summarize(after, keep, 200, client, "claude-haiku-5-5")


def drive(strategy: Summarize, conv: Conversation, turns: range) -> list[Any]:
    out = []
    for n in turns:
        tool_turn(conv, n, ("read_file", f"file {n} "))
        out.append(strategy.apply(conv))
    return out


def test_no_call_at_or_below_the_threshold() -> None:
    client, fake = summarizer("s")
    results = drive(summarize(client), Conversation(PROMPT), range(1, 4))
    assert fake.requests == [] and all(r.calls == () for r in results)
    assert all(len(r.messages) == 2 * n + 1 for n, r in zip(range(1, 4), results, strict=True))


def test_one_call_when_the_threshold_is_crossed_then_none_until_it_is_crossed_again() -> None:
    client, fake = summarizer("first", "second", "third")
    results = drive(summarize(client), Conversation(PROMPT), range(1, 10))
    called = [n for n, r in enumerate(results, start=1) if r.calls]
    # n=4 > 3 summarizes turns 1-2 (covered=2); next when n - covered > 3: n=6 (covered 4), n=8
    assert called == [4, 6, 8]
    assert len(fake.requests) == 3


def test_message_shape_after_a_summary() -> None:
    client, _ = summarizer("S1")
    strategy = summarize(client)
    results = drive(strategy, Conversation(PROMPT), range(1, 5))
    messages = results[-1].messages
    # turn 4: summary covers turns 1-2; kept: turns 3-4 (assistant + results each)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    first = messages[0]["content"]
    assert first[0] == {"type": "text", "text": PROMPT}
    assert "S1" in first[1]["text"] and "turns 1-2" in first[1]["text"]
    assert messages[1]["content"][0]["text"] == "step 3"


def test_summary_is_reused_between_triggers_and_the_prefix_only_grows() -> None:
    client, fake = summarizer("S1", "S2", "S3")
    strategy = summarize(client, after=4, keep=2)
    results = drive(strategy, Conversation(PROMPT), range(1, 9))
    # n=5 > 4 -> covered=3; n=8: 8-3=5 > 4 -> second call
    assert [bool(r.calls) for r in results] == [False] * 4 + [True, False, False, True]
    assert len(fake.requests) == 2
    for a, b in itertools.pairwise(results[4:7]):
        assert b.messages[: len(a.messages)] == a.messages  # append-only between summaries


def test_second_summary_folds_in_the_first_and_the_new_turns() -> None:
    client, fake = summarizer("S1", "S2")
    drive(summarize(client, after=4, keep=2), Conversation(PROMPT), range(1, 9))
    second = fake.requests[1]["messages"][0]["content"]
    assert "S1" in second  # previous summary
    assert "[turn 4]" in second and "[turn 6]" in second and "[turn 3]" not in second


def test_summary_request_has_no_tools_effort_low_and_a_token_cap() -> None:
    client, fake = summarizer("S1")
    drive(summarize(client), Conversation(PROMPT), range(1, 5))
    request = fake.requests[0]
    assert "tools" not in request
    assert request["output_config"] == {"effort": "low"}
    assert (request["model"], request["max_tokens"]) == ("claude-haiku-5-5", 200)
    text = request["messages"][0]["content"]
    assert PROMPT in text and "[turn 1] call read_file" in text and "[turn 2] result" in text
    assert "[turn 3]" not in text  # the kept turns are not summarized


def test_thinking_blocks_are_not_sent_to_the_summarizer() -> None:
    client, fake = summarizer("S1")
    conv = Conversation(PROMPT)
    for n in range(1, 5):
        tool_turn(conv, n, ("read_file", "r"), thinking=True)
    summarize(client).apply(conv)
    assert "hmm" not in fake.requests[0]["messages"][0]["content"]


def test_the_call_is_reported_as_data_for_the_caller_to_charge() -> None:
    client, _ = summarizer("S1")
    results = drive(summarize(client), Conversation(PROMPT), range(1, 5))
    (call,) = results[-1].calls
    assert call.kind == "summary" and (call.usage.input_tokens, call.usage.output_tokens) == (
        100,
        20,
    )


def test_fresh_gives_every_attempt_its_own_state() -> None:
    client, fake = summarizer("A", "B")
    shared = summarize(client)
    first, second = shared.fresh(), shared.fresh()
    drive(first, Conversation(PROMPT), range(1, 5))
    results = drive(second, Conversation(PROMPT), range(1, 4))
    assert all(r.calls == () for r in results)  # not contaminated by the first attempt
    assert len(fake.requests) == 1


def test_summarize_parameters_are_checked() -> None:
    client, _ = summarizer()
    for after, keep, tokens in ((2, 2, 100), (3, 0, 100), (3, 2, 0)):
        with pytest.raises(ValueError):
            Summarize(after, keep, tokens, client, "m")


def test_describe_names_the_strategy_and_parameters() -> None:
    config = AgentConfig(
        name="t",
        model="m",
        system_prompt="s",
        effort="low",
        tool_set="structured",
        max_turns=1,
        max_tokens_per_call=1,
        tool_output_max_chars=1,
        context_strategy="truncate_tool_outputs",
        context_keep_last=2,
    )
    assert ContextStrategy.describe(config) == "truncate_tool_outputs(keep_last=2)"
    assert ContextStrategy.describe(config.model_copy(update={"context_strategy": "full"})) == (
        "full(keep_last=2)"
    )
