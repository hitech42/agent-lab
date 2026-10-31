"""Per-attempt budget: stop an attempt once a token or cost cap is reached."""

from dataclasses import dataclass, field

from agent_lab.harness.pricing import ModelPrice, PriceTable
from agent_lab.harness.tracing import TokenUsage


@dataclass
class Budget:
    """Tracks spend for one attempt. A cap of None means unlimited.

    ``max_total_tokens`` counts all four token kinds (input, output, cache write,
    cache read). The budget is exceeded once spend reaches the cap.
    """

    max_cost_usd: float | None = None
    max_total_tokens: int | None = None
    price: ModelPrice | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)

    def __post_init__(self) -> None:
        if self.max_cost_usd is not None and self.price is None:
            raise ValueError("max_cost_usd requires a price")

    def record(self, usage: TokenUsage) -> None:
        self.usage = TokenUsage(
            input_tokens=self.usage.input_tokens + usage.input_tokens,
            output_tokens=self.usage.output_tokens + usage.output_tokens,
            cache_creation_input_tokens=self.usage.cache_creation_input_tokens
            + usage.cache_creation_input_tokens,
            cache_read_input_tokens=self.usage.cache_read_input_tokens
            + usage.cache_read_input_tokens,
        )

    @property
    def cost_usd(self) -> float | None:
        """Spend so far in USD, or None when no price is known."""
        return None if self.price is None else PriceTable.compute_cost(self.price, self.usage)

    def exceeded(self) -> bool:
        if self.max_total_tokens is not None and self.usage.total >= self.max_total_tokens:
            return True
        cost = self.cost_usd
        return self.max_cost_usd is not None and cost is not None and cost >= self.max_cost_usd
