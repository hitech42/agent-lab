from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_lab.benchmark.task import Task


def make(**overrides: object) -> Task:
    data: dict[str, object] = {
        "id": "demo-1",
        "title": "Demo",
        "category": "bugfix",
        "prompt": "Fix it.",
        "grading": "hidden_tests",
        "directory": Path("tasks/demo-1"),
    }
    return Task.model_validate({**data, **overrides})


def test_defaults() -> None:
    task = make()
    assert task.protected == ["tests/**"]
    assert task.canaries == []
    assert task.expected_answer is None


def test_is_frozen_and_forbids_unknown_fields() -> None:
    task = make()
    with pytest.raises(ValidationError):
        task.title = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        make(surprise=1)


@pytest.mark.parametrize("bad_id", ["", "Has-Caps", "-leading", "has space", "under_score"])
def test_id_must_be_a_slug(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        make(id=bad_id)


@pytest.mark.parametrize("category", ["bugfix", "feature", "refactor", "qa", "injection"])
def test_categories(category: str) -> None:
    assert make(category=category).category == category


def test_unknown_category_and_grading_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make(category="chore")
    with pytest.raises(ValidationError):
        make(grading="vibes")


def test_exact_answer_requires_expected_answer() -> None:
    with pytest.raises(ValidationError, match="expected_answer is required"):
        make(grading="exact_answer")
    with pytest.raises(ValidationError, match="expected_answer is required"):
        make(grading="exact_answer", expected_answer="  ")
    assert make(grading="exact_answer", expected_answer="42").expected_answer == "42"


def test_expected_answer_not_allowed_for_hidden_tests() -> None:
    with pytest.raises(ValidationError, match="only allowed for exact_answer"):
        make(expected_answer="42")


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "a/../../x", "", "a\\b"])
def test_protected_and_canary_paths_must_be_relative_and_contained(bad: str) -> None:
    with pytest.raises(ValidationError):
        make(protected=[bad])
    with pytest.raises(ValidationError):
        make(canaries=[bad])


def test_custom_protected_and_canaries() -> None:
    task = make(protected=["src/**", "*.cfg"], canaries=["PWNED.txt"])
    assert task.protected == ["src/**", "*.cfg"]
    assert task.canaries == ["PWNED.txt"]
