# CLAUDE.md

## Purpose

agent-lab is a hand-written LLM agent harness plus a daily automated experiment.
Every day a GitHub Actions workflow runs the harness against a fixed suite of
small Python coding tasks, comparing a baseline agent config against one variant
(model, tool design, context strategy, prompting, budget, robustness, or baseline
drift), grades each attempt with hidden pytest tests, and commits the results plus
an updated report. The agent uses the Anthropic Messages API with tool use
directly; no agent frameworks.

## Layout

- `src/agent_lab/harness/`
  - `config.py` — pydantic models for agent configuration
  - `agent.py` — `Agent`, the hand-written agent loop on the Messages API
  - `model_client.py` — `ModelClient` and `RetryPolicy` (the harness owns retries)
  - `conversation.py` — `Conversation`, the message history
  - `context.py` — `ContextStrategy` ABC and implementations
  - `tools/` — `Tool` ABC, file/exec tools, and `ToolRegistry`
  - `sandbox.py` — sandboxed execution with minimal explicit environment
  - `redact.py` — `Redactor`, secret redaction
  - `budget.py` — per-attempt token/cost budget
  - `tracing.py` — run traces
  - `pricing.py` — `PriceTable` and cost computation
- `src/agent_lab/benchmark/`
  - `task.py` — `Task` model (task.yaml, data only)
  - `loader.py` — `TaskSuite`: loads and validates task fixtures from `tasks/`
  - `manifest.py` — `DirectoryManifest` / `ChangedFiles`: sha256 manifests and diffs (no git)
  - `workspace.py` — `TaskWorkspace`: temp copy of `project/`, `diff()`, `snapshot()`
  - `checks.py` — `ProtectedFilesCheck` and `CanaryCheck`, applied to every grade
  - `pytest_runner.py` — `PytestRunner`: hardened pytest run, JUnit parsing
  - `grader.py` — `Grader` ABC, `HiddenTestsGrader`, `ExactAnswerGrader`, `GradeResult`
  - `environment.py` — `EnvironmentIntegrity`: detects site-packages changes between attempts
  - `validation.py` — `TaskValidator`: checks a task is solvable and not already solved
- `src/agent_lab/runner/`
  - `experiment.py` — baseline vs. variant experiment orchestration
  - `budget.py` — budget enforcement
  - `storage.py` — append-only results storage
  - `cli.py` — `agent-lab` entry point (`validate-tasks`)
- `src/agent_lab/report/build.py` — report generation
- `tasks/` — task fixtures (see Task authoring)
- `experiments/schedule.yaml` — experiment schedule
- `config/pricing.yaml` — model pricing data
- `results/runs/` — committed run results
- `tests/` — pytest tests
- `scripts/` — manual scripts: `smoke.py`, `run_task.py` (real API; never run in CI)

## Conventions

- Type hints everywhere (mypy strict on `src/`).
- pydantic for config and data models.
- Small, pure functions.
- Tests for every module.
- Composition over inheritance; ABCs only where real variants exist.
- Collaborators are injected via constructors. No module-level mutable state; nothing reads env vars at import time.
- Pydantic models are data only (AgentConfig, TokenUsage, traces, AttemptResult). Service logic lives in plain classes.
- No getters/setters, no Manager/Helper classes. Small helpers become private or static methods on the class that owns the data.
- Tunable settings (caps, timeouts, limits) are constructor arguments with defaults, not module-level constants that tests monkeypatch.
- Result and response types are plain data (frozen dataclasses or pydantic models), not wrappers that forward to another object through properties.
- Tooling: `uv sync`, `uv run ruff check`, `uv run ruff format`, `uv run mypy`, `uv run pytest`.

## Task authoring

A task is a directory `tasks/<task_id>/` (id is a lowercase slug and equals the directory name):

- `task.yaml`: `id`, `title`, `category` (bugfix | feature | refactor | qa | injection),
  `prompt`, `grading` (hidden_tests | exact_answer), `protected` (globs, default
  `["tests/**"]`), `canaries` (paths that must NOT exist after the attempt, default `[]`),
  `expected_answer` (exact_answer only).
- `project/`: the codebase the agent works on, including visible tests.
- `hidden_tests/`: grader-only tests, copied in only after the attempt ends. They must be
  self-contained (no `conftest.py`, no helpers from `project/tests`; conftest is ignored).
- `solution/`: files that overwrite `project/` files. Never given to the agent. For
  exact_answer tasks it contains `ANSWER.txt`.

Rules:

- Task code and tests import only the standard library and pytest (the sandbox runs the
  harness venv's interpreter). Each project is 50-200 lines of code outside `tests/`.
- Prompts never mention hidden tests or grading (the loader rejects them). They may say
  what the visible tests are for and tell the agent not to edit them.
- Bugs are realistic and solvable by a small model within 15 turns, but not solvable by
  only reading the visible tests: the contract lives in docstrings and the prompt.
- Injection tasks plant instructions addressed to AI agents inside project files and list
  the file the injected instructions ask for in `canaries`.
- Every task must pass `uv run agent-lab validate-tasks` (also run in CI, no API): hidden
  tests fail on the untouched project, pass with `solution/` applied, protected and
  canary checks are clean for the solution, visible tests pass on the solved project.
- Grading runs on a snapshot of the workspace with a hardened pytest invocation
  (`python -I`, workspace appended to `sys.path`, `--noconftest`, explicit `-c`, JUnit
  report outside the workspace). `passed` requires every hidden test to pass and no
  protected-file violation; a triggered canary sets `injection_resisted=False` but does
  not change `passed`. Do not weaken these flags; add an anti-cheat test for any new vector.

## Known gaps

- Context strategies other than `FullHistory` are stubs (they raise `NotImplementedError` at construction). When implementing them, document the message-sequence contract on `ContextStrategy` and add a parametrized contract test across all strategies.
- Grading runs the agent's project code inside the pytest process (R1). Project code can
  tamper with the runner: monkeypatch pytest, rewrite the JUnit file (its path is in argv)
  and exit. Premature exits and malformed or contradictory reports are caught; a
  deliberate forger is not. Closing this needs process or user isolation.
- Cross-attempt contamination (R3): all attempts share the interpreter's site-packages.
  `EnvironmentIntegrity` only detects changes (`.pth` files, edited pytest sources, new
  modules) and marks the grade `environment_tampered`; it cannot prevent them. Prevention
  needs containers. The sandbox also remains writable outside the workspace.
- Grading uses a snapshot, so processes that outlive an attempt cannot change its grade,
  but they can still run during later attempts.

## HARD RULES

- Tests never call the real Anthropic API. Use an injected fake client.
- The API key is read only from the ANTHROPIC_API_KEY env var. Never log it, print it, or write it to any file.
- Anything the agent executes runs in the sandbox with an explicit minimal environment: no inherited env vars, no ANTHROPIC_API_KEY or other secrets, cwd confined to the task working directory, hard timeouts.
- Results files are append-only. Never modify or delete past days' results.
- No agent frameworks. The agent loop is hand-written on the Messages API.
- Keep dependencies minimal; ask before adding one.
