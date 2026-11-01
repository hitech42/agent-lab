"""Fakes and builders for the runner tests. No network, no real model.

A plain module (not a conftest): tests/harness and tests/benchmark have their own helpers
and sibling conftest modules would shadow each other.
"""

import copy
import io
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import yaml
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from agent_lab.benchmark.environment import EnvironmentIntegrity
from agent_lab.benchmark.grader import Grader, GradeResult
from agent_lab.benchmark.loader import TaskSuite
from agent_lab.benchmark.manifest import ChangedFiles
from agent_lab.benchmark.task import Task
from agent_lab.benchmark.validation import TaskValidator
from agent_lab.harness.model_client import ModelClient, RetryPolicy
from agent_lab.harness.pricing import PriceTable
from agent_lab.harness.redact import Redactor
from agent_lab.runner.cli import Cli
from agent_lab.runner.database import ResultsDatabase
from agent_lab.runner.estimator import CostEstimator
from agent_lab.runner.experiment import ExperimentRunner
from agent_lab.runner.plan import PlanOptions, RunPlan, RunPlanner
from agent_lab.runner.provenance import Provenance
from agent_lab.runner.run_command import DbCommand, RunCommand, RunDependencies
from agent_lab.runner.schedule import ExperimentSchedule
from agent_lab.runner.storage import ResultsStore, TranscriptStore
from agent_lab.runner.warmup import CacheWarmer
from agent_lab.runner.wiring import AgentFactory, MissingApiKeyError

REPO = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = REPO / "experiments" / "schedule.yaml"
TASKS_DIR = REPO / "tasks"
PROVENANCE = Provenance(
    git_sha="abc123",
    uv_lock_sha256="f" * 64,
    python_version="3.14.0",
    anthropic_sdk_version="9.9.9",
    source="local",
)
MONDAY = "2026-11-02"  # ISO weekday 1: model-comparison
SUNDAY = "2026-11-08"  # ISO weekday 7: aa-test


def prices() -> PriceTable:
    return PriceTable.from_yaml(PriceTable.default_path())


def schedule() -> ExperimentSchedule:
    return ExperimentSchedule.from_yaml(SCHEDULE_PATH, prices())


def suite() -> TaskSuite:
    return TaskSuite.load(TASKS_DIR)


def make_plan(day: str = MONDAY, **options: Any) -> RunPlan:
    return RunPlanner(schedule(), suite()).plan(date.fromisoformat(day), PlanOptions(**options))


def with_caps(plan: RunPlan, daily: float | None = None, attempt: float | None = None) -> RunPlan:
    update: dict[str, float] = {}
    if daily is not None:
        update["daily_max_cost_usd"] = daily
    if attempt is not None:
        update["attempt_max_cost_usd"] = attempt
    return plan.model_copy(update={"resolved": plan.resolved.model_copy(update=update)})


# --- model fakes -------------------------------------------------------------------------


def usage(i: int = 100, o: int = 20, cw: int | None = None, cr: int | None = None) -> Usage:
    return Usage(
        input_tokens=i,
        output_tokens=o,
        cache_creation_input_tokens=cw,
        cache_read_input_tokens=cr,
    )


def _message(content: list[Any], stop_reason: str, u: Usage) -> Message:
    return Message(
        id="msg_test",
        content=content,
        model="fake",
        role="assistant",
        stop_reason=stop_reason,  # type: ignore[arg-type]
        type="message",
        usage=u,
    )


def text_response(text: str = "done", u: Usage | None = None) -> Message:
    return _message([TextBlock(type="text", text=text)], "end_turn", u or usage())


def tool_use_response(call_id: str, name: str, tool_input: dict[str, Any]) -> Message:
    block = ToolUseBlock(type="tool_use", id=call_id, name=name, input=tool_input)
    return _message([block], "tool_use", usage())


def status_error(code: int) -> anthropic.APIStatusError:
    response = httpx2.Response(code, request=httpx2.Request("POST", "https://example.invalid"))
    return anthropic.APIStatusError(f"http {code}", response=response, body=None)


def auth_error() -> anthropic.AuthenticationError:
    response = httpx2.Response(401, request=httpx2.Request("POST", "https://example.invalid"))
    return anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)


Responder = Callable[[dict[str, Any]], Any]


def is_warmup(request: dict[str, Any]) -> bool:
    return request["max_tokens"] == 1


def default_responder(request: dict[str, Any]) -> Any:
    """Warm-up writes the cache; every attempt just answers and stops."""
    if is_warmup(request):
        return text_response("ok", usage(i=10, o=1, cw=500))
    return text_response("done", usage(i=1000, o=200, cr=300))


class ResponderClient:
    """A raw client whose answers come from a function of the request. Records requests."""

    def __init__(self, responder: Responder = default_responder) -> None:
        self.responder = responder
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(copy.deepcopy(kwargs))
        outcome = self.responder(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def warmups(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if is_warmup(r)]

    @property
    def attempts(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if not is_warmup(r)]


def agent_factory(client: ResponderClient, redactor: Redactor | None = None) -> AgentFactory:
    model_client = ModelClient(client, RetryPolicy(sleep=lambda _: None))  # type: ignore[arg-type]
    return AgentFactory(model_client, prices(), redactor or Redactor([]))


# --- other fakes -------------------------------------------------------------------------


class StepClock:
    """A clock that starts at a fixed UTC time and advances a step on every reading."""

    def __init__(self, start: str = "2026-11-02T09:00:00", step_seconds: int = 1) -> None:
        self._now = datetime.fromisoformat(start).replace(tzinfo=UTC)
        self._step = timedelta(seconds=step_seconds)

    def now(self) -> datetime:
        current = self._now
        self._now += self._step
        return current


class FixedClock:
    """A clock that always reads the same moment (to force abort-file name clashes)."""

    def __init__(self, at: str = "2026-11-02T09:00:00") -> None:
        self._at = datetime.fromisoformat(at).replace(tzinfo=UTC)

    def now(self) -> datetime:
        return self._at


@dataclass
class FakeGrader:
    """Stands in for a Grader: canned result, optional per-call behaviour."""

    log: list[str] = field(default_factory=list)
    passed: bool = True
    tamper_from_call: int | None = None
    raise_on_call: int | None = None
    calls: int = 0

    def grade(self, task: Task, workspace: Any, sandbox: Any, final_text: str) -> GradeResult:
        self.calls += 1
        self.log.append(task.id)
        if self.raise_on_call == self.calls:
            raise RuntimeError("grader exploded sk-ant-leaky_secret-1")
        tampered = self.tamper_from_call is not None and self.calls >= self.tamper_from_call
        return GradeResult(
            passed=self.passed and not tampered,
            tests_passed=3 if self.passed else 1,
            tests_failed=0 if self.passed else 2,
            tests_errors=0,
            tests_total=3,
            protected_violations=[],
            canaries_triggered=[],
            injection_resisted=True if task.category == "injection" else None,
            environment_tampered=tampered,
            changed_files=ChangedFiles(modified=["x.py"]),
            detail="3 passed" if self.passed else "2 failed",
        )


@dataclass
class World:
    runner: ExperimentRunner
    store: ResultsStore
    transcripts_dir: Path
    client: ResponderClient
    grader: FakeGrader
    integrity: EnvironmentIntegrity
    site_dir: Path
    plan: RunPlan


def build_world(
    tmp_path: Path,
    *,
    plan: RunPlan | None = None,
    responder: Responder = default_responder,
    grader: FakeGrader | None = None,
    history: list[Any] | None = None,
    clock: Any = None,
    max_consecutive_errors: int = 3,
    redactor: Redactor | None = None,
    results_dir: Path | None = None,
    store: ResultsStore | None = None,
    grader_factory: Callable[[Task, EnvironmentIntegrity], Any] | None = None,
) -> World:
    """A runner over real tasks with a fake model and a fake grader, writing under tmp_path."""
    plan = plan or make_plan(trials=1)
    client = ResponderClient(responder)
    agents = agent_factory(client, redactor)
    fake_grader = grader or FakeGrader()
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "pkg.py").write_text("x = 1\n")
    integrity = EnvironmentIntegrity([site_dir])
    store = store or ResultsStore(results_dir or tmp_path / "results")
    runner = ExperimentRunner(
        agents=agents,
        suite=suite(),
        grader_factory=(
            (lambda task: grader_factory(task, integrity))
            if grader_factory
            else (lambda task: fake_grader)  # type: ignore[arg-type,return-value]
        ),
        integrity=integrity,
        store=store,
        transcripts=TranscriptStore(tmp_path / "transcripts"),
        estimator=CostEstimator(history or [], schedule().cost_defaults, prices()),
        warmer=CacheWarmer(agents.client, agents.prices),
        provenance=PROVENANCE,
        clock=clock or StepClock(),
    )
    return World(
        runner, store, tmp_path / "transcripts", client, fake_grader, integrity, site_dir, plan
    )


def write_schedule(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    """Copy the real schedule and prompts into tmp_path after applying ``mutate`` to the YAML."""
    import shutil

    raw = yaml.safe_load(SCHEDULE_PATH.read_text())
    mutate(raw)
    directory = tmp_path / "experiments"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SCHEDULE_PATH.parent / "prompts", directory / "prompts", dirs_exist_ok=True)
    path = directory / "schedule.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def iter_lines(path: Path) -> Iterator[str]:
    yield from path.read_text().splitlines()


@dataclass
class CliWorld:
    """A Cli wired to fakes, plus everything a test wants to inspect afterwards."""

    cli: Cli
    out: io.StringIO
    client: ResponderClient
    grader: FakeGrader
    tmp_path: Path
    environ: dict[str, str]

    @property
    def results_dir(self) -> Path:
        return self.tmp_path / "results"

    def run(self, *args: str, track_dir: bool = True) -> tuple[int, str]:
        """Run `agent-lab run ...` with the real tasks/schedule and tmp results/transcripts."""
        argv = ["run", "--tasks-dir", str(TASKS_DIR), "--schedule", str(SCHEDULE_PATH)]
        if track_dir:
            argv += [
                "--results-dir",
                str(self.results_dir),
                "--transcripts-dir",
                str(self.tmp_path / "transcripts"),
            ]
        start = self.out.tell()
        code = self.cli.run([*argv, *args])
        return code, self.out.getvalue()[start:]


def build_cli(
    tmp_path: Path,
    *,
    responder: Responder = default_responder,
    grader: FakeGrader | None = None,
    api_key: str | None = "test-key-value",
    clock: Any = None,
) -> CliWorld:
    """The real Cli and RunCommand over fakes: no network, no real grading."""
    out = io.StringIO()
    client = ResponderClient(responder)
    fake_grader = grader or FakeGrader()
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir(parents=True, exist_ok=True)
    environ: dict[str, str] = {"ANTHROPIC_API_KEY": api_key} if api_key else {}

    def factory(env: Any) -> AgentFactory:
        if not env.get("ANTHROPIC_API_KEY"):
            raise MissingApiKeyError("ANTHROPIC_API_KEY is not set")
        return agent_factory(client, Redactor.from_env(env))

    deps = RunDependencies(
        clock=clock or StepClock(),
        environ=environ,
        repo_root=REPO,
        prices=prices,
        agent_factory=factory,
        integrity=lambda: EnvironmentIntegrity([site_dir]),
        grader_factory=lambda integrity: lambda task: fake_grader,  # type: ignore[arg-type,return-value]
    )
    cli = Cli(
        TaskValidator(Grader.for_task),
        RunCommand(deps, out),
        DbCommand(ResultsDatabase(), out),
        out,
    )
    return CliWorld(cli, out, client, fake_grader, tmp_path, environ)
