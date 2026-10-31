from anthropic.types import TextBlock, ToolUseBlock

from agent_lab.harness.conversation import Conversation


def test_starts_with_task_prompt_and_alternates_roles() -> None:
    conv = Conversation("task")
    conv.add_assistant([{"type": "text", "text": "ok"}])
    conv.add_tool_results([{"type": "tool_result", "tool_use_id": "x", "content": "c"}])
    conv.add_user_text("more")
    assert [m["role"] for m in conv.messages] == ["user", "assistant", "user", "user"]
    assert conv.messages[0] == {"role": "user", "content": "task"}


def test_add_assistant_is_lossless_for_sdk_blocks_and_dicts() -> None:
    conv = Conversation("t")
    conv.add_assistant(
        [
            TextBlock(type="text", text="thinking out loud"),
            ToolUseBlock(type="tool_use", id="t1", name="bash", input={"command": "ls"}),
            {"type": "thinking", "thinking": "hm", "signature": "sig", "extra": None},
        ]
    )
    content = conv.messages[1]["content"]
    assert content[0] == {"type": "text", "text": "thinking out loud"}
    assert content[1] == {
        "type": "tool_use",
        "id": "t1",
        "name": "bash",
        "input": {"command": "ls"},
    }
    # all fields kept (so thinking blocks round-trip); only None values dropped
    assert content[2] == {"type": "thinking", "thinking": "hm", "signature": "sig"}


def test_messages_returns_a_copy() -> None:
    conv = Conversation("t")
    conv.add_assistant([{"type": "text", "text": "a"}])
    snapshot = conv.messages
    snapshot[0]["content"] = "mutated"
    snapshot[1]["content"][0]["text"] = "mutated"
    snapshot.append({"role": "user", "content": "extra"})
    assert conv.messages[0]["content"] == "t"
    assert conv.messages[1]["content"][0]["text"] == "a"
    assert len(conv.messages) == 2


def test_last_assistant_text() -> None:
    conv = Conversation("t")
    assert conv.last_assistant_text() == ""
    conv.add_assistant([{"type": "text", "text": "first"}])
    conv.add_tool_results([{"type": "tool_result", "tool_use_id": "x", "content": "c"}])
    assert conv.last_assistant_text() == "first"  # skips trailing user messages
    conv.add_assistant(
        [
            {"type": "text", "text": "se"},
            {"type": "tool_use", "id": "i", "name": "n", "input": {}},
            {"type": "text", "text": "cond"},
        ]
    )
    assert conv.last_assistant_text() == "second"
