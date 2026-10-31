import time

import pytest
from conftest import FakeClient, status_error, text_response, usage

from agent_lab.harness.model_client import ModelClient, ModelRequest, RetryPolicy


def request() -> ModelRequest:
    return ModelRequest(
        model="m", max_tokens=10, system=[{"type": "text", "text": "s"}], tools=[], messages=[]
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
    assert sleeps == [1.0, 2.0]


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
    fake = FakeClient([status_error(500)] * 3)
    with pytest.raises(Exception, match="boom"):
        ModelClient(fake, RetryPolicy(sleep=lambda _: None)).create(request())
    assert len(fake.requests) == 3
