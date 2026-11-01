"""The daily spending cap across warm-up and all attempts."""


class DailyBudget:
    """Tracks the day's spend (including earlier segments of a resumed day)."""

    def __init__(self, cap_usd: float, spent_usd: float = 0.0) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd

    def can_afford(self, estimate_usd: float) -> bool:
        return self.spent_usd + estimate_usd <= self.cap_usd

    def record(self, cost_usd: float) -> None:
        self.spent_usd += cost_usd
