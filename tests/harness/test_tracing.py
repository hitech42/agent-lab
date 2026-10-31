import pytest

from agent_lab.harness.tracing import AttemptTrace, ToolCallTrace, TurnTrace


def turn(n: int, calls: list[ToolCallTrace]) -> TurnTrace:
    return TurnTrace(
        turn=n,
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=1,
        cache_read_input_tokens=2,
        stop_reason="tool_use",
        latency_s=0.5,
        total_latency_s=0.75,
        retries=n - 1,
        tool_calls=calls,
    )


def test_trace_aggregation() -> None:
    trace = AttemptTrace(
        turns=[
            turn(1, [ToolCallTrace(name="a", duration_s=0.1, is_error=True)]),
            turn(2, [ToolCallTrace(name="b", duration_s=0.2, is_error=False)]),
        ]
    )
    assert trace.total_input_tokens == 20 and trace.total_output_tokens == 10
    assert trace.total_cache_creation_input_tokens == 2
    assert trace.total_cache_read_input_tokens == 4
    assert trace.total_latency_s == pytest.approx(1.5)
    assert trace.total_retries == 1
    assert trace.tool_call_count == 2 and trace.tool_error_count == 1
    assert trace.total_usage().total == 36
    assert "total:" in trace.summary()
    assert trace.model_dump()["tool_call_count"] == 2
