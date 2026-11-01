"""Estimate what a plan will cost, from history where there is some."""

import json
from collections import defaultdict
from collections.abc import Iterable

from agent_lab.harness.config import AgentConfig
from agent_lab.harness.pricing import PriceTable
from agent_lab.harness.redact import Redactor
from agent_lab.harness.tools import ToolRegistry
from agent_lab.runner.plan import PlannedAttempt, RunPlan
from agent_lab.runner.records import AttemptRecord
from agent_lab.runner.schedule import ExperimentSchedule


class CostEstimator:
    """Mean historical cost per (config_hash, task), then (model, task), then a default."""

    _CHARS_PER_TOKEN = 4

    def __init__(
        self,
        records: Iterable[AttemptRecord],
        cost_defaults: dict[str, float],
        prices: PriceTable,
    ) -> None:
        by_hash: dict[tuple[str, str], list[float]] = defaultdict(list)
        by_model: dict[tuple[str, str], list[float]] = defaultdict(list)
        for record in records:
            if record.cost_usd > 0:
                by_hash[(record.config_hash, record.task_id)].append(record.cost_usd)
                by_model[(record.model, record.task_id)].append(record.cost_usd)
        self._by_hash = {key: sum(v) / len(v) for key, v in by_hash.items()}
        self._by_model = {key: sum(v) / len(v) for key, v in by_model.items()}
        self._defaults = dict(cost_defaults)
        self._prices = prices

    def attempt_cost(self, config: AgentConfig, task_id: str) -> float:
        config_hash = ExperimentSchedule.config_hash(config)
        if (config_hash, task_id) in self._by_hash:
            return self._by_hash[(config_hash, task_id)]
        if (config.model, task_id) in self._by_model:
            return self._by_model[(config.model, task_id)]
        return self._defaults[config.model]

    def planned_cost(self, plan: RunPlan, planned: PlannedAttempt) -> float:
        config = self._config_of(plan, planned)
        return self.attempt_cost(config, planned.task_id)

    def plan_cost(self, plan: RunPlan) -> float:
        return sum(self.planned_cost(plan, planned) for planned in plan.attempts)

    def warmup_cost(self, config: AgentConfig) -> float:
        """Cost of one warm-up call: the cached prefix written once plus one output token."""
        tools = ToolRegistry.for_set(config.tool_set, Redactor([]), 1).api_params()
        prefix_chars = len(json.dumps(tools)) + len(config.system_prompt)
        price = self._prices.price(config.model)
        tokens = prefix_chars / self._CHARS_PER_TOKEN
        return (tokens * price.cache_write + price.output) / 1_000_000

    def warmup_cost_for_plan(self, plan: RunPlan) -> float:
        return sum(self.warmup_cost(config) for config in self._distinct_configs(plan))

    @staticmethod
    def _config_of(plan: RunPlan, planned: PlannedAttempt) -> AgentConfig:
        resolved = plan.resolved
        return resolved.baseline if planned.config_role == "baseline" else resolved.variant

    @staticmethod
    def _distinct_configs(plan: RunPlan) -> list[AgentConfig]:
        distinct: dict[str, AgentConfig] = {}
        for config in (plan.resolved.baseline, plan.resolved.variant):
            distinct.setdefault(ExperimentSchedule.config_hash(config), config)
        return list(distinct.values())
