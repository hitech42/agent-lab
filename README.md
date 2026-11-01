# agent-lab

An LLM agent harness plus a daily automated experiment. Each day a
scheduled GitHub Actions workflow runs the harness (built directly on the
Anthropic Messages API, no agent frameworks) against a fixed suite of small
Python coding tasks, compares a baseline agent config with one variant, grades
attempts with hidden pytest tests, and commits the results and an updated report.

## How it runs

A scheduled GitHub Actions workflow (`.github/workflows/daily.yml`) runs the experiment twice a
day, a primary and a backup; the second run is a no-op once the day is finished. Each weekday has
one question (for example "does the structured tool set beat a single bash tool?"): the harness
runs a baseline agent configuration and one variant on the same small Python tasks, several
trials each, and grades every attempt with hidden pytest tests. Agent commands run as a separate,
network-less Linux user, and the job that runs them cannot push: a second job validates the
results and commits them.

Results are committed under `results/` and are append-only: `results/runs/<date>.jsonl` (one line
per attempt), `<date>.run.json` (the day's summary), `<date>.abort-*.json` (aborted attempts that
were resumed) and `results/configs/<hash>.json` (every configuration that ran, prompt included).
`agent-lab db rebuild` turns them into a local SQLite database for ad-hoc queries.

**Status:** the harness, tasks, runner, isolation and the daily workflow are written and tested,
but nothing has run against the real Anthropic API yet, so `results/` is still empty, and there is
no generated report yet (`src/agent_lab/report/` is a stub).

## License

MIT
