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


def compute_cost(price: ModelPrice, usage: TokenUsage) -> float:
    """Cost in USD for the given usage."""
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    ) / _PER_MILLION


class PriceTable:
    """Per-model prices, loaded from config/pricing.yaml."""

    def __init__(self, prices: dict[str, ModelPrice]) -> None:
        self._prices = dict(prices)

    @classmethod
    def from_yaml(cls, path: Path) -> PriceTable:
        try:
            raw = yaml.safe_load(path.read_text())
            return cls(PricingFile.model_validate(raw).models)
        except Exception as exc:
            raise PricingError(f"could not load pricing from {path}: {exc}") from exc

    @staticmethod
    def default_path() -> Path:
        return Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"

    def price(self, model: str) -> ModelPrice:
        try:
            return self._prices[model]
        except KeyError:
            raise PricingError(f"no pricing for model {model!r}") from None

    def cost(self, model: str, usage: TokenUsage) -> float:
        return compute_cost(self.price(model), usage)
