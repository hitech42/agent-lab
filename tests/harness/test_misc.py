from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_lab.harness.budget import Budget
from agent_lab.harness.config import AgentConfig
from agent_lab.harness.context import apply_context_strategy
from agent_lab.harness.pricing import (
    ModelPrice,
    PricingError,
    compute_cost,
    default_pricing_path,
    get_price,
    load_prices,
)
from agent_lab.harness.redact import redact, redact_obj
from agent_lab.harness.tracing import AttemptTrace, TokenUsage, ToolCallTrace, TurnTrace


def test_config_validation_and_frozen(config: AgentConfig) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(**{**config.model_dump(), "max_turns": 0})
    with pytest.raises(ValidationError):
        AgentConfig(**{**config.model_dump(), "tool_set": "other"})
    with pytest.raises(ValidationError):
        config.max_turns = 5  # type: ignore[misc]
    assert config.context_strategy == "full"


def test_context_strategies() -> None:
    msgs = [{"role": "user", "content": "x"}]
    assert apply_context_strategy("full", msgs) is msgs
    for s in ("truncate_tool_outputs", "summarize"):
        with pytest.raises(NotImplementedError):
            apply_context_strategy(s, msgs)  # type: ignore[arg-type]


def test_trace_aggregation() -> None:
    def turn(n: int, calls: list[ToolCallTrace]) -> TurnTrace:
        return TurnTrace(
            turn=n,
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=1,
            cache_read_input_tokens=2,
            stop_reason="tool_use",
            latency_s=0.5,
            retries=n - 1,
            tool_calls=calls,
        )

    trace = AttemptTrace(
        turns=[
            turn(1, [ToolCallTrace(name="a", duration_s=0.1, is_error=True)]),
            turn(2, [ToolCallTrace(name="b", duration_s=0.2, is_error=False)]),
        ]
    )
    assert trace.total_input_tokens == 20 and trace.total_output_tokens == 10
    assert trace.total_cache_creation_input_tokens == 2
    assert trace.total_cache_read_input_tokens == 4
    assert trace.total_latency_s == pytest.approx(1.0)
    assert trace.total_retries == 1
    assert trace.tool_call_count == 2 and trace.tool_error_count == 1
    assert trace.total_usage().total == 36
    assert "total:" in trace.summary()
    assert trace.model_dump()["tool_call_count"] == 2


def test_pricing_math_and_loading(tmp_path: Path) -> None:
    price = ModelPrice(input=1, output=2, cache_write=3, cache_read=4)
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_creation_input_tokens=100_000,
        cache_read_input_tokens=250_000,
    )
    assert compute_cost(price, usage) == pytest.approx(1 + 1 + 0.3 + 1)

    f = tmp_path / "p.yaml"
    f.write_text("models:\n  m: {input: 1, output: 2, cache_write: 3, cache_read: 4}\n")
    prices = load_prices(f)
    assert get_price(prices, "m") == price
    with pytest.raises(PricingError):
        get_price(prices, "unknown")
    f.write_text("models: {m: {input: 1}}\n")
    with pytest.raises(PricingError):
        load_prices(f)


def test_repo_pricing_yaml_loads() -> None:
    prices = load_prices(default_pricing_path())
    assert {"claude-haiku-5-5", "claude-sonnet-5-5"} <= set(prices)
    assert "VERIFY" in default_pricing_path().read_text()


def test_budget() -> None:
    with pytest.raises(ValueError):
        Budget(max_cost_usd=1.0)
    b = Budget(max_total_tokens=10)
    assert not b.exceeded() and b.cost_usd is None
    b.record(TokenUsage(input_tokens=6, output_tokens=4))
    assert b.exceeded()
    assert not Budget().exceeded()


def test_redact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_lab.harness.redact._API_KEY", "not-matching-pattern-1234")
    assert redact("a not-matching-pattern-1234 b") == "a [REDACTED] b"
    assert redact("tok sk-ant-api03-x_y-Z end") == "tok [REDACTED] end"
    assert redact_obj({"a": ["sk-ant-zzz", 3, {"b": "not-matching-pattern-1234"}]}) == {
        "a": ["[REDACTED]", 3, {"b": "[REDACTED]"}]
    }
    monkeypatch.setattr("agent_lab.harness.redact._API_KEY", None)
    assert redact("plain text") == "plain text"
