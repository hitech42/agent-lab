"""The Task model: one benchmark task described by tasks/<id>/task.yaml."""

from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TaskCategory = Literal["bugfix", "feature", "refactor", "qa", "injection"]
GradingKind = Literal["hidden_tests", "exact_answer"]
Difficulty = Literal["easy", "medium", "hard"]


class Task(BaseModel):
    """Validated task description (data only).

    ``directory`` is set by the loader; it is never read from task.yaml.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    PROJECT: ClassVar[str] = "project"
    HIDDEN_TESTS: ClassVar[str] = "hidden_tests"
    SOLUTION: ClassVar[str] = "solution"
    ANSWER_FILE: ClassVar[str] = "ANSWER.txt"

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    category: TaskCategory
    difficulty: Difficulty
    prompt: str = Field(min_length=1)
    grading: GradingKind
    protected: list[str] = Field(default_factory=lambda: ["tests/**"])
    canaries: list[str] = Field(default_factory=list)
    injection_markers: list[str] = Field(default_factory=list)
    expected_answer: str | None = None
    directory: Path

    @field_validator("protected", "canaries")
    @classmethod
    def _relative_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            path = PurePosixPath(value)
            if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
                raise ValueError(f"{value!r} must be a relative POSIX path without '..'")
        return values

    @field_validator("injection_markers")
    @classmethod
    def _distinct_markers(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            key = " ".join(value.split()).casefold()
            if not key:
                raise ValueError("injection_markers must not contain a blank marker")
            if key in seen:
                raise ValueError(f"injection_markers has {value!r} twice (case-insensitive)")
            seen.add(key)
        return values

    @model_validator(mode="after")
    def _markers_match_category(self) -> Self:
        if self.category == "injection" and not self.injection_markers:
            raise ValueError("injection_markers is required (non-empty) for an injection task")
        if self.category != "injection" and self.injection_markers:
            raise ValueError("injection_markers is only allowed for an injection task")
        return self

    @model_validator(mode="after")
    def _answer_matches_grading(self) -> Self:
        if self.grading == "exact_answer" and not (self.expected_answer or "").strip():
            raise ValueError("expected_answer is required for exact_answer grading")
        if self.grading == "hidden_tests" and self.expected_answer is not None:
            raise ValueError("expected_answer is only allowed for exact_answer grading")
        return self
