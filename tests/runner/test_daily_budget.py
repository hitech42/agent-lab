from agent_lab.runner.budget import DailyBudget


def test_can_afford_until_the_cap() -> None:
    budget = DailyBudget(1.0)
    assert budget.can_afford(1.0) and not budget.can_afford(1.01)
    budget.record(0.6)
    assert budget.can_afford(0.4) and not budget.can_afford(0.41)
    assert budget.spent_usd == 0.6


def test_starts_from_earlier_spend() -> None:
    budget = DailyBudget(1.0, spent_usd=0.9)
    assert budget.can_afford(0.1) and not budget.can_afford(0.11)
