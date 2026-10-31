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
  - `tools.py` — tool definitions and dispatch
  - `sandbox.py` — sandboxed execution with minimal explicit environment
  - `loop.py` — the hand-written agent loop on the Messages API
  - `context.py` — context management strategies
  - `tracing.py` — run traces
  - `pricing.py` — token pricing and cost computation
- `src/agent_lab/benchmark/`
  - `loader.py` — loads task fixtures from `tasks/`
  - `grader.py` — grades attempts with hidden pytest tests
- `src/agent_lab/runner/`
  - `experiment.py` — baseline vs. variant experiment orchestration
  - `budget.py` — budget enforcement
  - `storage.py` — append-only results storage
  - `cli.py` — command-line entry point
- `src/agent_lab/report/build.py` — report generation
- `tasks/` — task fixtures (format TBD)
- `experiments/schedule.yaml` — experiment schedule
- `config/pricing.yaml` — model pricing data
- `results/runs/` — committed run results
- `tests/` — pytest tests
- `scripts/` — helper scripts

## Conventions

- Type hints everywhere (mypy strict on `src/`).
- pydantic for config and data models.
- Small, pure functions.
- Tests for every module.
- Tooling: `uv sync`, `uv run ruff check`, `uv run ruff format`, `uv run mypy`, `uv run pytest`.

## HARD RULES

- Tests never call the real Anthropic API. Use an injected fake client.
- The API key is read only from the ANTHROPIC_API_KEY env var. Never log it, print it, or write it to any file.
- Anything the agent executes runs in the sandbox with an explicit minimal environment: no inherited env vars, no ANTHROPIC_API_KEY or other secrets, cwd confined to the task working directory, hard timeouts.
- Results files are append-only. Never modify or delete past days' results.
- No agent frameworks. The agent loop is hand-written on the Messages API.
- Keep dependencies minimal; ask before adding one.
