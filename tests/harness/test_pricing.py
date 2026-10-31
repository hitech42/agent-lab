from pathlib import Path

import pytest

from agent_lab.harness.pricing import ModelPrice, PriceTable, PricingError
from agent_lab.harness.tracing import TokenUsage

PRICE = ModelPrice(input=1, output=2, cache_write=3, cache_read=4)
USAGE = TokenUsage(
    input_tokens=1_000_000,
    output_tokens=500_000,
    cache_creation_input_tokens=100_000,
    cache_read_input_tokens=250_000,
)


def test_compute_cost_math() -> None:
    assert PriceTable.compute_cost(PRICE, USAGE) == pytest.approx(1 + 1 + 0.3 + 1)


def test_price_table_lookup_and_cost() -> None:
    table = PriceTable({"m": PRICE})
    assert table.price("m") == PRICE
    assert table.cost("m", USAGE) == pytest.approx(3.3)
    with pytest.raises(PricingError, match="no pricing for model 'unknown'"):
        table.price("unknown")
    with pytest.raises(PricingError):
        table.cost("unknown", USAGE)


def test_from_yaml(tmp_path: Path) -> None:
    f = tmp_path / "p.yaml"
    f.write_text("models:\n  m: {input: 1, output: 2, cache_write: 3, cache_read: 4}\n")
    assert PriceTable.from_yaml(f).price("m") == PRICE
    f.write_text("models: {m: {input: 1}}\n")
    with pytest.raises(PricingError):
        PriceTable.from_yaml(f)
    with pytest.raises(PricingError):
        PriceTable.from_yaml(tmp_path / "missing.yaml")


def test_repo_pricing_yaml_loads() -> None:
    path = PriceTable.default_path()
    table = PriceTable.from_yaml(path)
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5"):
        table.price(model)
    assert "VERIFY" in path.read_text()
