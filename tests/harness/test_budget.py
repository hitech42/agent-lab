import pytest

from agent_lab.harness.budget import Budget
from agent_lab.harness.pricing import ModelPrice
from agent_lab.harness.tracing import TokenUsage

PRICE = ModelPrice(input=1.0, output=2.0, cache_write=3.0, cache_read=0.5)


def test_cost_cap_requires_a_price() -> None:
    with pytest.raises(ValueError, match="max_cost_usd requires a price"):
        Budget(max_cost_usd=1.0)


def test_token_cap() -> None:
    b = Budget(max_total_tokens=10)
    assert not b.exceeded() and b.cost_usd is None
    b.record(TokenUsage(input_tokens=6, output_tokens=4))
    assert b.exceeded()


def test_cost_cap_and_cost_usd() -> None:
    b = Budget(max_cost_usd=0.5, price=PRICE)
    b.record(TokenUsage(input_tokens=400_000))
    assert not b.exceeded() and b.cost_usd == pytest.approx(0.4)
    b.record(TokenUsage(input_tokens=100_000))
    assert b.exceeded()


def test_unlimited_budget_never_exceeded() -> None:
    b = Budget()
    b.record(TokenUsage(input_tokens=10**9))
    assert not b.exceeded()
