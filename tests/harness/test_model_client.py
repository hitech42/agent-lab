import time

import anthropic
import httpx2
import pytest
from conftest import FakeClient, status_error, text_response, usage

from agent_lab.harness.model_client import ModelClient, ModelRequest, RetryPolicy


def request() -> ModelRequest:
    return ModelRequest(
        model="m",
        max_tokens=10,
        system=[{"type": "text", "text": "s"}],
        tools=[],
        messages=[],
        effort="high",
    )


def test_is_transient() -> None:
    assert RetryPolicy.is_transient(status_error(429))
    assert RetryPolicy.is_transient(status_error(500))
    assert RetryPolicy.is_transient(status_error(529))
    assert not RetryPolicy.is_transient(status_error(400))
    assert not RetryPolicy.is_transient(status_error(401))
    assert not RetryPolicy.is_transient(ValueError("x"))


def test_call_returns_retry_count_and_backs_off() -> None:
    sleeps: list[float] = []
    calls = iter([status_error(503), status_error(429), "ok"])

    def fn() -> str:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    assert RetryPolicy(sleep=sleeps.append).call(fn) == ("ok", 2)
    assert sleeps == [2.0, 4.0]


def test_call_gives_up_after_max_attempts_and_reraises() -> None:
    attempts = 0

    def fn() -> None:
        nonlocal attempts
        attempts += 1
        raise status_error(500)

    with pytest.raises(Exception, match="boom"):
        RetryPolicy(max_attempts=3, sleep=lambda _: None).call(fn)
    assert attempts == 3


def test_call_does_not_retry_non_transient() -> None:
    attempts = 0

    def fn() -> None:
        nonlocal attempts
        attempts += 1
        raise status_error(400)

    with pytest.raises(Exception, match="boom"):
        RetryPolicy(sleep=lambda _: None).call(fn)
    assert attempts == 1


def test_create_builds_request_and_returns_response_data() -> None:
    fake = FakeClient([text_response("hi", usage(3, 4, cw=5, cr=None))])
    resp = ModelClient(fake, RetryPolicy()).create(request())

    assert fake.requests[0] == {
        "model": "m",
        "max_tokens": 10,
        "system": [{"type": "text", "text": "s"}],
        "tools": [],
        "messages": [],
        "output_config": {"effort": "high"},
    }
    assert resp.stop_reason == "end_turn" and resp.retries == 0
    u = resp.usage
    assert (u.input_tokens, u.output_tokens) == (3, 4)
    assert (u.cache_creation_input_tokens, u.cache_read_input_tokens) == (5, 0)  # None -> 0
    assert resp.tool_calls == []


def test_latency_split_includes_backoff_only_in_total() -> None:
    fake = FakeClient([status_error(503), text_response()])
    client = ModelClient(fake, RetryPolicy(sleep=lambda s: time.sleep(0.2)))
    resp = client.create(request())

    assert resp.retries == 1
    assert resp.total_latency_s >= 0.2
    assert 0 <= resp.latency_s < 0.2
    assert resp.latency_s <= resp.total_latency_s


def test_create_reraises_after_retries_exhausted() -> None:
    fake = FakeClient([status_error(500)] * 4)
    with pytest.raises(Exception, match="boom"):
        ModelClient(fake, RetryPolicy(sleep=lambda _: None)).create(request())
    assert len(fake.requests) == 4


def test_the_default_policy_makes_four_attempts_and_waits_two_four_eight_seconds() -> None:
    sleeps: list[float] = []

    def fn() -> None:
        raise status_error(503)

    with pytest.raises(Exception, match="boom"):
        RetryPolicy(sleep=sleeps.append).call(fn)
    assert sleeps == [2.0, 4.0, 8.0]


def retry_waits(*errors: Exception, **policy: float) -> list[float]:
    """The waits a policy makes before a final success, given the errors it meets first."""
    sleeps: list[float] = []
    pending = list(errors)

    def fn() -> str:
        if pending:
            raise pending.pop(0)
        return "ok"

    RetryPolicy(max_attempts=len(errors) + 1, sleep=sleeps.append, **policy).call(fn)
    return sleeps


def test_a_retry_after_header_replaces_the_backoff() -> None:
    assert retry_waits(status_error(429, {"retry-after": "7"})) == [7.0]
    assert retry_waits(status_error(429, {"retry-after": "0.5"})) == [0.5]
    assert retry_waits(status_error(429, {"retry-after": "0"})) == [0.0]


def test_retry_after_is_capped_at_sixty_seconds() -> None:
    assert retry_waits(status_error(429, {"retry-after": "600"})) == [60.0]
    assert retry_waits(status_error(529, {"retry-after": "600"}), max_retry_after=5.0) == [5.0]


@pytest.mark.parametrize("value", ["soon", "-3", "nan", "inf", "Wed, 21 Oct 2026 07:28:00 GMT", ""])
def test_an_unusable_retry_after_falls_back_to_the_backoff(value: str) -> None:
    assert retry_waits(status_error(503, {"retry-after": value})) == [2.0]


def test_without_a_header_or_a_response_the_backoff_applies() -> None:
    assert retry_waits(status_error(503)) == [2.0]
    timeout = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x.invalid"))
    assert retry_waits(timeout, status_error(503, {"retry-after": "3"})) == [2.0, 3.0]


def test_retry_after_is_used_per_error_not_per_call() -> None:
    errors = [status_error(429, {"retry-after": "9"}), status_error(500)]
    assert retry_waits(*errors) == [9.0, 4.0]  # the second wait is the attempt-1 backoff


def test_every_request_carries_its_effort() -> None:
    fake = FakeClient([text_response("a"), text_response("b")])
    client = ModelClient(fake, RetryPolicy())
    client.create(request())
    client.create(ModelRequest(**{**request().__dict__, "effort": "low"}))
    assert [r["output_config"] for r in fake.requests] == [{"effort": "high"}, {"effort": "low"}]
