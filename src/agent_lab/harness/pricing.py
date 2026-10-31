"""Token pricing lookup and cost computation."""

from pathlib import Path

import yaml
from pydantic import BaseModel

from agent_lab.harness.tracing import TokenUsage

_PER_MILLION = 1_000_000


class PricingError(Exception):
    """Raised for unknown models or malformed pricing data."""


class ModelPrice(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


class PricingFile(BaseModel):
    models: dict[str, ModelPrice]


def default_pricing_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"


def load_prices(path: Path) -> dict[str, ModelPrice]:
    try:
        raw = yaml.safe_load(path.read_text())
        return PricingFile.model_validate(raw).models
    except Exception as exc:
        raise PricingError(f"could not load pricing from {path}: {exc}") from exc


def get_price(prices: dict[str, ModelPrice], model: str) -> ModelPrice:
    try:
        return prices[model]
    except KeyError:
        raise PricingError(f"no pricing for model {model!r}") from None


def compute_cost(price: ModelPrice, usage: TokenUsage) -> float:
    """Cost in USD for the given usage."""
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    ) / _PER_MILLION
