"""Builders for the triage tests: grades, results, conversations and a scripted model."""

import json
from pathlib import Path
from typing import Any

from anthropic.types import TextBlock
from runner_support import ResponderClient, _message, agent_factory, text_response, usage

from agent_lab.benchmark.grader import GradeResult
from agent_lab.benchmark.manifest import ChangedFiles
from agent_lab.benchmark.task import Task
from agent_lab.harness.agent import AttemptResult, AttemptStatus
from agent_lab.harness.redact import Redactor
from agent_lab.harness.tracing import AttemptTrace
from agent_lab.runner.triage import ModelTriage

TASK = Task(
    id="fix-thing",
    title="Fix the thing",
    category="bugfix",
    difficulty="easy",
    prompt="Fix the bug in thing.py. Do not edit the tests.",
    grading="hidden_tests",
    directory=Path("/nonexistent"),
)


def grade(
    *,
    passed: bool = False,
    failed_tests: list[str] | None = None,
    passed_tests: list[str] | None = None,
    protected: list[str] | None = None,
    canaries: list[str] | None = None,
    tampered: bool = False,
    detail: str = "1 failed",
) -> GradeResult:
    return GradeResult(
        passed=passed,
        tests_passed=len(passed_tests or []),
        tests_failed=len(failed_tests or []),
        tests_errors=0,
        tests_total=len(passed_tests or []) + len(failed_tests or []),
        protected_violations=protected or [],
        canaries_triggered=canaries or [],
        injection_resisted=None,
        environment_tampered=tampered,
        changed_files=ChangedFiles(modified=["thing.py"]),
        detail=detail,
        passed_tests=passed_tests or [],
        failed_tests=failed_tests or [],
    )


def tool_use(call_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}],
    }


def tool_result(call_id: str, text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": False}
        ],
    }


def conversation(ran_tests: bool) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": TASK.prompt},
        tool_use("a", "read_file", {"path": "thing.py"}),
        tool_result("a", "code"),
        tool_use("b", "str_replace_edit", {"path": "thing.py"}),
        tool_result("b", "ok"),
    ]
    if ran_tests:
        messages += [tool_use("c", "run_tests", {}), tool_result("c", "1 failed\n[exit code: 1]")]
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "done"}]})
    return messages


def result(
    status: AttemptStatus = "completed",
    *,
    ran_tests: bool = True,
    messages: list[dict[str, Any]] | None = None,
    final_text: str = "I fixed it.",
) -> AttemptResult:
    return AttemptResult(
        status=status,
        stop_reason="end_turn",
        final_text=final_text,
        trace=AttemptTrace(),
        cost_usd=0.01,
        messages=conversation(ran_tests) if messages is None else messages,
    )


def model_reply(text: str, **usage_kwargs: int) -> Any:
    return _message([TextBlock(type="text", text=text)], "end_turn", usage(**usage_kwargs))


def verdict(label: str, note: str = "a note") -> Any:
    return model_reply(json.dumps({"label": label, "note": note}), i=2000, o=300)


def model_triage(
    replies: list[Any] | Any, redactor: Redactor | None = None, **options: Any
) -> tuple[ModelTriage, ResponderClient]:
    """A ModelTriage over a scripted raw client (a list is replayed, an exception is raised)."""
    script = list(replies) if isinstance(replies, list) else None

    def responder(request: dict[str, Any]) -> Any:
        if script is None:
            return replies
        return script.pop(0)

    client = ResponderClient(responder)
    factory = agent_factory(client, redactor)
    return ModelTriage(factory.client, factory.prices, factory.redactor, **options), client


__all__ = ["text_response"]
