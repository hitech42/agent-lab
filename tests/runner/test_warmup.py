from runner_support import (
    ResponderClient,
    agent_factory,
    auth_error,
    make_plan,
    prices,
    status_error,
    text_response,
    usage,
)

from agent_lab.runner.schedule import ExperimentSchedule
from agent_lab.runner.warmup import CacheWarmer


def agents_for(plan, factory):  # type: ignore[no-untyped-def]
    resolved = plan.resolved
    return {
        ExperimentSchedule.config_hash(c): factory.agent(c)
        for c in (resolved.baseline, resolved.variant)
    }


def test_one_minimal_call_per_distinct_config() -> None:
    client = ResponderClient()
    factory = agent_factory(client)
    result = CacheWarmer(factory.client, prices()).warm(agents_for(make_plan(trials=1), factory))
    assert result.calls == 2 and result.failure is None
    assert [r["model"] for r in client.warmups] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert all(r["max_tokens"] == 1 for r in client.requests)


def test_the_aa_test_warms_up_once() -> None:
    client = ResponderClient()
    factory = agent_factory(client)
    plan = make_plan("2026-11-08", trials=1)
    result = CacheWarmer(factory.client, prices()).warm(agents_for(plan, factory))
    assert result.calls == 1 and len(client.warmups) == 1


def test_warmup_request_has_the_same_cached_prefix_as_an_attempt() -> None:
    import tempfile
    from pathlib import Path

    from agent_lab.harness.sandbox import Sandbox
    from agent_lab.runner.wiring import AgentFactory  # noqa: F401

    client = ResponderClient()
    factory = agent_factory(client)
    plan = make_plan(trials=1)
    agent = factory.agent(plan.resolved.baseline)
    with tempfile.TemporaryDirectory() as tmp, Sandbox(Path(tmp)) as sandbox:
        agent.run("a task", sandbox, factory.budget(plan.resolved.baseline, 1.0))
    (attempt,) = client.attempts
    warm = agent.warmup_request()
    assert warm.system == attempt["system"] and warm.tools == attempt["tools"]
    assert warm.system[0]["cache_control"] == {"type": "ephemeral"}
    assert warm.tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert warm.max_tokens == 1 and warm.model == attempt["model"]


def test_cost_is_computed_from_the_usage_of_each_call() -> None:
    def responder(request):  # type: ignore[no-untyped-def]
        return text_response("ok", usage(i=1_000_000, o=1_000_000, cw=1_000_000, cr=1_000_000))

    client = ResponderClient(responder)
    factory = agent_factory(client)
    plan = make_plan("2026-11-08", trials=1)  # a single distinct config: haiku
    result = CacheWarmer(factory.client, prices()).warm(agents_for(plan, factory))
    haiku = prices().price("claude-haiku-5-5")
    assert result.cost_usd == haiku.input + haiku.output + haiku.cache_write + haiku.cache_read


def test_an_auth_error_is_reported_with_its_type_and_status() -> None:
    client = ResponderClient(lambda request: auth_error())
    factory = agent_factory(client)
    result = CacheWarmer(factory.client, prices()).warm(agents_for(make_plan(trials=1), factory))
    assert result.failure is not None
    assert result.failure.reason == "warmup_failed"
    assert (result.failure.error_type, result.failure.error_status) == ("AuthenticationError", 401)
    assert result.calls == 0 and len(client.requests) == 1  # stops at the first failure


def test_other_errors_are_retried_then_reported() -> None:
    client = ResponderClient(lambda request: status_error(500))
    factory = agent_factory(client)
    result = CacheWarmer(factory.client, prices()).warm(agents_for(make_plan(trials=1), factory))
    assert result.failure is not None and result.failure.error_status == 500
    assert len(client.requests) == 4  # RetryPolicy's four attempts, then give up
