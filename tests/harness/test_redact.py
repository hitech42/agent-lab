import importlib

import pytest

from agent_lab.harness import redact as redact_module
from agent_lab.harness.redact import Redactor


def test_redacts_exact_value_even_if_it_does_not_match_the_pattern() -> None:
    r = Redactor(["not-matching-pattern-1234"])
    assert r.redact("a not-matching-pattern-1234 b") == "a [REDACTED] b"


def test_redacts_sk_ant_pattern() -> None:
    assert Redactor([]).redact("tok sk-ant-api03-x_y-Z end") == "tok [REDACTED] end"


def test_redact_obj_recurses_dicts_and_lists() -> None:
    r = Redactor(["not-matching-pattern-1234"])
    obj = {"a": ["sk-ant-zzz", 3, {"b": "not-matching-pattern-1234"}]}
    assert r.redact_obj(obj) == {"a": ["[REDACTED]", 3, {"b": "[REDACTED]"}]}


def test_plain_text_and_empty_secrets_are_untouched() -> None:
    assert Redactor([""]).redact("plain text") == "plain text"
    assert Redactor([]).redact("") == ""


def test_longest_secret_wins_when_one_contains_another() -> None:
    r = Redactor(["abc", "abcdef"])
    assert r.redact("x abcdef y") == "x [REDACTED] y"


def test_from_env_reads_env_at_call_time() -> None:
    assert Redactor.from_env({"ANTHROPIC_API_KEY": "my-key-value"}).redact("my-key-value") == (
        "[REDACTED]"
    )
    assert Redactor.from_env({}).redact("my-key-value") == "my-key-value"


def test_from_env_defaults_to_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "process-env-key")
    assert Redactor.from_env().redact("process-env-key") == "[REDACTED]"


def test_importing_the_module_stores_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "import-time-key")
    reloaded = importlib.reload(redact_module)
    try:
        assert "import-time-key" not in repr(vars(reloaded))
        assert reloaded.Redactor([]).redact("import-time-key") == "import-time-key"
    finally:
        monkeypatch.undo()
        importlib.reload(redact_module)
