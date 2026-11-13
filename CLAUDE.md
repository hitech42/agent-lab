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
  - `agent.py` — `Agent`, the hand-written agent loop on the Messages API; every request,
    the cache warm-up included, sends `output_config={"effort": config.effort}`
  - `model_client.py` — `ModelClient` and `RetryPolicy` (the harness owns retries: 4 attempts,
    2s/4s/8s backoff, a numeric `retry-after` header replaces the wait, capped at 60s)
  - `conversation.py` — `Conversation`, the message history
  - `context.py` — `ContextStrategy` ABC (the contract), `ContextResult`, `FullHistory`, `TruncateToolOutputs`,
    `Summarize` (see Context strategies)
  - `tools/` — `Tool` ABC, file/exec tools, and `ToolRegistry`
  - `sandbox.py` — sandboxed execution with minimal explicit environment
  - `isolation.py` — `CommandIsolation` ABC, `NoIsolation`, `LinuxUserIsolation` (commands as a
    dedicated user, no network, processes killed after every command)
  - `redact.py` — `Redactor`, secret redaction
  - `budget.py` — per-attempt token/cost budget
  - `tracing.py` — run traces (`TurnTrace` per turn, `ContextCallTrace` for extra context calls)
  - `pricing.py` — `PriceTable` and cost computation
- `src/agent_lab/benchmark/`
  - `task.py` — `Task` model (task.yaml, data only; `difficulty` easy | medium | hard)
  - `loader.py` — `TaskSuite`: loads and validates task fixtures from `tasks/`; `fingerprint()`
    gives the suite and task content hashes (see Suite versioning)
  - `manifest.py` — `DirectoryManifest` / `ChangedFiles`: sha256 manifests and diffs (no git)
  - `workspace.py` — `TaskWorkspace`: temp copy of `project/`, `diff()`, `snapshot()`
  - `checks.py` — `ProtectedFilesCheck` and `CanaryCheck`, applied to every grade
  - `pytest_runner.py` — `PytestRunner`: hardened pytest run, JUnit parsing
  - `grader.py` — `Grader` ABC, `HiddenTestsGrader`, `ExactAnswerGrader`, `GradeResult`
  - `environment.py` — `EnvironmentIntegrity`: detects site-packages and `tasks/` changes between attempts
  - `validation.py` — `TaskValidator`: checks a task is solvable and not already solved, within
    the line limit of its difficulty tier
- `src/agent_lab/runner/`
  - `schedule.py` — `ExperimentSchedule`: validated baseline, weekday tracks, `config_hash`
  - `plan.py` — `RunPlanner`: deterministic, interleaved `RunPlan` for a date
  - `estimator.py` — `CostEstimator`: history-based cost estimates with per-model defaults
  - `warmup.py` — `CacheWarmer`: one cache warm-up call per distinct config (also the canary)
  - `budget.py` — `DailyBudget`: the day's spending cap
  - `experiment.py` — `ExperimentRunner`: runs a plan, records each attempt immediately
  - `outcome.py` — `RunReport`: kind, status, a redacted workflow-safe detail and the attempt counts
    of a run, written by `--outcome-file` for the workflow (see Daily workflow)
  - `records.py` — `AttemptRecord`, `RunSummary`, `AbortRecord`, `RecordBuilder`
  - `provenance.py` — `ProvenanceReader`: git sha, lockfile hash, versions, ci|local
  - `storage.py` — `ResultsStore` (append-only results) and `TranscriptStore`
  - `calibration.py` — `CalibrationReport`: per-task pass rate, turns and cost of a config, for
    `scripts/calibrate.py` (see Calibration)
  - `database.py` — `ResultsDatabase`: derived SQLite database (never committed)
  - `wiring.py` — `AgentFactory`: client (`max_retries=0`, 300s request timeout), prices,
    redactor; shared with scripts
  - `publish.py` — `ResultsPublisher` (validates an untrusted incoming directory, then copies it
    into `results/`) and `CommitMessageBuilder`
  - `run_command.py` — `RunCommand`, `DbCommand`, `ResultsCommand`: the logic behind the CLI commands
  - `cli.py` — `agent-lab` entry point (`validate-tasks`, `run`, `db rebuild`,
    `results publish|commit-message`, `report build`)
- `src/agent_lab/report/` — the dashboard and charts built from `results/` (see Report)
  - `stats.py` — pure statistics: Wilson, Newcombe (paired difference), bootstrap, verdict wording
  - `model.py` — pydantic data models; `ReportData` is what `summary.json` serializes
  - `analysis.py` — `ResultsAnalyzer`: pairs, exclusions, series, spend
  - `markdown.py` — `MarkdownRenderer` and `ReadmeUpdater` (the README markers)
  - `charts.py` — `TracksChart` and `DailySpendChart` (SVG)
  - `build.py` — `ReportBuilder` (pure: files in memory); `command.py` — `ReportCommand` (I/O, `--check`)
- `README.md` — hand-written text plus the generated dashboard between the report markers
- `report/` — generated: `tracks.svg`, `daily.svg`, `summary.json` (committed)
- `.github/workflows/` — `ci.yml` (checks, isolation tests) and `daily.yml` (the daily run)
- `tasks/` — task fixtures (see Task authoring)
- `experiments/schedule.yaml` — baseline, defaults and one track per ISO weekday
- `experiments/prompts/` — system prompts referenced by the schedule
- `config/pricing.yaml` — model pricing data
- `results/runs/` — committed results: `<date>.jsonl`, `<date>.run.json`, `<date>.abort-*.json`
- `results/configs/<config_hash>.json` — committed registry of every resolved config that ran
  (prompt text included, `name` excluded: exactly what the hash covers)
- `tests/` — pytest tests
- `scripts/` — manual scripts: `smoke.py`, `run_task.py`, `calibrate.py` (real API; never run in CI) and
  `setup_sandbox_user.sh` (CI: creates the sandbox user for `--isolation linux-user`)

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
- Runner and report tests use the tiny stand-in tasks in `tests/runner/fixtures/tasks/` (same
  ids, categories and tiers as the first three real tasks), so they do not change when the real
  suite does; only tests that need real grading use `tasks/`.
- Tests marked `@pytest.mark.isolation` (`tests/isolation/`) need Linux, passwordless sudo and
  the sandbox user, and are skipped everywhere else; CI runs them in their own job with
  `uv run pytest -m isolation`. They are also `slow`.
- Tests that spawn real pytest subprocesses (grading, anti-cheat, task validation) are marked
  `@pytest.mark.slow` (or `pytestmark = pytest.mark.slow` for a whole module). The default
  `uv run pytest` skips them (`addopts = -m 'not slow'`) for a fast local loop. Run everything,
  as CI does, with `uv run pytest -m "slow or not slow"`; run only the slow ones with
  `uv run pytest -m slow`. Run the full suite before opening a PR.

## Running experiments

`uv run agent-lab run [--date YYYY-MM-DD] [--track NAME] [--trials N] [--tasks id,id]
[--results-dir DIR] [--transcripts-dir DIR] [--resume] [--dry-run] [--outcome-file FILE]`. The API key comes from
the `ANTHROPIC_API_KEY` environment variable only. `--dry-run` prints the track, question,
resolved configs with their `config_hash`, the ordered plan and the cost estimate, makes no
API calls and writes no files (no key needed).

**Who writes `results/`:** only CI, and only through the daily workflow's `publish` job (see
Daily workflow; the `heartbeat` job appends to `results/heartbeat.log` only). Local runs always use `--results-dir /tmp/somewhere` so they never pollute the
repository. Transcripts
(`.transcripts/`, gitignored) and the derived database (`*.db`, gitignored) are never committed.

**Schedule:** `experiments/schedule.yaml` has the baseline config, defaults and exactly one
track per ISO weekday (Mon=1). A track is a name, a one-sentence question, `variant` overrides
of the baseline, and optional caps. Overrides must be real `AgentConfig` fields with registered
values (models in `config/pricing.yaml`, existing prompt files in `experiments/prompts/`).
`config_hash` is the first 12 hex of sha256 over the canonical JSON of the resolved config
(prompt text included, `name` excluded), so identical configs group across days and renames.

**Run lifecycle:**
1. If `<date>.run.json` exists the day is done: print "already done", exit 0, no API call.
2. If `<date>.jsonl` exists without a `.run.json` and `--resume` is absent: refuse (exit 1).
3. Load and validate schedule and tasks, pick the track (UTC date, or `--track`), plan the
   attempts: for each trial and task the baseline and variant run back to back, in an order
   shuffled with `random.Random(<date string>)`, so a date always gives the same plan.
4. Estimate the cost (history per config/task, else per-model defaults) and capture
   `EnvironmentIntegrity` once. With `--resume`, refuse (exit 1) if the day's existing records or
   readable abort files belong to another run id or track, if a role's `config_hash` differs
   from the one already recorded (the schedule or a prompt was edited since), or if the records'
   `suite_hash` differs from the current suite's (a task was edited since; records written before
   suites were versioned count as different): resuming would mix configurations or suites in
   one day. `--trials` must be at least 1; `--tasks` must have no empty
   or duplicate ids.
5. Register each resolved config in `results/configs/<config_hash>.json` (exclusive create,
   skipped if it exists), then warm the prompt cache with one minimal call per distinct config. Its cost counts against
   the daily budget. It is also the canary: any warm-up failure aborts before any attempt.
6. Run the attempts. Before the first attempt of a pair, check that spent + the estimate of
   both attempts fits the daily cap; otherwise stop and mark the rest `skipped_budget`. Each
   attempt gets its own `Budget` (attempt cap), is graded on a snapshot, and its record is
   appended and fsynced immediately. The attempt `status` follows the last stop reason:
   `completed` only for `end_turn`/`stop_sequence`; `truncated` for `max_tokens` or
   `model_context_window_exceeded`; `refused` for `refusal`; `incomplete` for anything else that
   is not `tool_use` (`pause_turn`, unknown); plus `max_turns`, `budget_exceeded`, `error`. The
   stop reason itself is always recorded, and only `error` counts toward the abort streak. An exception inside an attempt is recorded as that
   attempt's `error` and the run continues. A transcript is written per attempt.
7. Outcomes. **Terminal** outcomes write `<date>.run.json` (exclusive create) and the day is
   done: `completed`, `partial_budget` (budget stop), `aborted_tampered` (site-packages
   changed; final). **Aborts are events**: an authentication/permission error (in the warm-up
   or an attempt), a 400 whose message says the credit balance is too low (reason `credit_balance`,
   `error_type` kept as recorded; the wording is the `credit_balance_text` constructor argument of
   `ExperimentRunner`), a warm-up failure, or 3 consecutive attempts with `status == "error"`
   (failed grades never count) write `<date>.abort-<HHMMSS>.json` (exclusive create; the
   summary fields plus `reason`, `error_type`, `error_status`, completed ids and
   `skipped_attempts`) and do NOT write `.run.json`, so the day stays resumable.
8. Exit 0 whenever a record was written or the day was already done; nonzero only for
   configuration errors or crashes before any record could be written. An abort therefore exits 0
   too; with `--outcome-file FILE` the run writes `kind=` (`summary`, `abort`, `already_done`,
   `dry_run`), `status=` (the summary status, or the abort reason), `detail=` (`<http status>
   <error type>: <message>`, redacted, only `[A-Za-z0-9 .,:_'()/-]`, no `::`, at most 160 chars) and,
   for summaries and aborts, `completed=` / `planned=`. The daily workflow reads that file. Nothing is
   written on configuration errors (exit 1).

| Day | Track | Variant (vs baseline) | Daily cap | Expected cost* |
|---|---|---|---|---|
| Mon | `model-comparison` | `model: claude-sonnet-5-5` | $3.00 (attempt $0.50) | about $1.5 |
| Tue | `tool-design` | `tool_set: bash_only` | $1.00 | about $0.3 |
| Wed | `context-truncate` | `context_strategy: truncate_tool_outputs`, `context_keep_last: 2` | $1.00 | about $0.3 |
| Thu | `turn-budget` | `max_turns: 6` | $1.00 | about $0.3 |
| Fri | `self-verify` | `system_prompt: self_verify.md` | $1.00 | about $0.3 |
| Sat | `effort-sweep` | `effort: medium` | $1.00 | about $0.3 |
| Sun | `aa-test` | none (identical configs) | $1.00 | about $0.3 |

*8 tasks x 4 trials per side. Default attempt cap $0.15. Track names are unique per behavior: one
weekday each. The baseline is haiku at **effort low**, so every track compares against that, and
Monday compares haiku-low with sonnet-low. `cost_defaults` (haiku 0.004, sonnet 0.04 per attempt)
follow the calibration means; the estimator uses history per config hash once there is some.

**Baseline effort (calibration outcome).** At effort low, haiku passed 78% of the 8-task suite
(25/32; easy 92%, medium 75%, hard 67%), against about 90% at medium; sonnet at low passed 100%.
78% is slightly above the 60-75% target band, but within noise (Wilson 95% interval about
61-89%), and the daily runs will refine it. The tasks were not made easier to get there: the
effort setting did the lowering. Changing the baseline effort changes its `config_hash`, so every
track started a new report series. Per attempt about $0.004 (haiku low) and $0.04 (sonnet low).

**Resuming:** Phase 4 always calls `agent-lab run --resume`, which is safe unconditionally: with
no JSONL for the date it is a normal run, with a finished day it prints "already done", and
after an abort it skips attempt ids already recorded and appends the rest to the same
JSONL. The final `RunSummary` of a day that had aborts has `abort_count` > 0, and its cost
and warm-up totals cover all segments. To re-run a terminal day, remove its `.run.json` by hand.

**Results layout and rules** (`results/runs/`):
- `<date>.jsonl`: one flat `AttemptRecord` per line (attempts that ran; skipped attempts are
  never written here). Fields include the full `GradeResult`, usage, cost, latency, retries,
  `error_type`/`error_status`, and provenance (`git_sha`, `uv_lock_sha256`, versions, `source`).
- `<date>.run.json`: the terminal `RunSummary`, including `skipped_attempts` (id, task, role,
  trial, reason) because the plan cannot be re-derived once the schedule changes.
- `<date>.abort-<HHMMSS>.json`: one per abort event.
- `corrupt_lines` on a summary or abort file counts unreadable JSONL lines and abort files found
  for that date (a file that cannot be read cannot contribute its warm-up cost, so the daily
  budget may be slightly under-counted); the CLI prints a warning when it is not 0.
- `results/configs/<config_hash>.json`: the resolved config of every hash that appears in a
  record, written once.
- Append-only: the store only appends or creates exclusively; nothing deletes, truncates or
  rewrites a result file. Never edit past days' files.
- **Schema compatibility:** result files are never rewritten, so old lines must keep parsing.
  Only add fields, and every new field needs a default (old files lack it). Never rename, remove
  or retype a field. A breaking change bumps `schema_version` and keeps a reader for the old one.
  `tests/runner/fixtures/*_v1.*` are real v1 files; `tests/runner/test_compatibility.py` checks
  that they parse and that no field missing from them is required. Do not edit the fixtures.
- Reading is tolerant, writing is strict: lines are split on `\n` and decoded one at a time, so a
  truncated multi-byte character costs only that line; unreadable lines and files are counted,
  never fatal. Appends loop until every byte is written.
- `uv run agent-lab db rebuild [--db results.db] [--results-dir results]` rebuilds a derived SQLite
  database (tables `attempts`, `runs`, `aborts`, `configs`, view `pairs` of baseline/variant per
  run, task and trial). Nothing depends on it.

## Daily workflow

`.github/workflows/daily.yml` runs the experiment unattended and publishes the results to `main`.
It has separate jobs so that the job that executes agent code can never push:

- **`experiment`** (`contents: read`, `environment: experiment`, 60 min): checkout with
  `persist-credentials: false`; uv installs into `/opt/agentlab` (`UV_PROJECT_ENVIRONMENT`,
  `UV_PYTHON_INSTALL_DIR`); `sudo scripts/setup_sandbox_user.sh`; then
  `agent-lab run --resume --isolation linux-user` (plus `--dry-run` when the `dry_run` input is
  set). `ANTHROPIC_API_KEY` is in the env of that one step only. Afterwards, even if the run failed,
  it copies the files that are new or changed under `results/runs` and `results/configs`
  (`git ls-files -m -o`) into an artifact (retention 3 days; skipped when nothing changed).
  `.transcripts/` is never uploaded.
- **`publish`** (`contents: write`, 10 min, no environment, no key, no sandbox, no agent code; runs
  only if the artifact exists and `dry_run` is false): checks out `main`, downloads the artifact,
  runs `agent-lab results publish`, then `agent-lab report build`, and if `results/` changed commits
  only `results/`, `report/` and `README.md` (author and
  committer from the repository variables `COMMIT_AUTHOR_NAME` / `COMMIT_AUTHOR_EMAIL`, which
  must be set), with the message from `agent-lab results commit-message`, then pushes; on a
  rejected push it `pull --rebase`s and retries, three pushes at most.
- **`heartbeat`** (`contents: write`, 5 min, no environment, no key, no sandbox, no agent code, no
  uv; runs only if the `experiment` job succeeded, `changed` is `false` and `dry_run` is false, so
  never together with `publish`): every slot after the first of a UTC day finds the day finished
  and has nothing to publish, so this job appends one line to `results/heartbeat.log` and commits
  and pushes it (`chore(heartbeat): no new results for <date> (run <id>)`, same author variables
  and push retry as `publish`), so every run shows on the contribution graph. The log is outside
  `results/runs` and `results/configs`, so nothing reads it; it is the one file in `results/` that
  `publish` does not write, and it is only ever appended to.
- **`alert`** (`needs: [experiment, publish]`, no token, no key, no checkout, 5 min): an abort or a
  tampered day still exits 0, so its record is collected and published first; this last job then
  fails the run so that GitHub notifies. The `experiment` job exposes the outcome file as job outputs
  (`kind`, `status`, `detail`, `completed`, `planned`; the "Expose the outcome" step lets through only
  lines of the expected shape and treats a missing or malformed file as `kind=unknown`). `alert`
  runs when `!cancelled()` (so also when `publish` was skipped) and the run is not a dry run, and only
  fires for: `kind == abort` (`::error::<reason>: <status> <type>: <message>`, e.g. `warmup_failed:
  400 BadRequestError: Your credit balance is too low`), `status == aborted_tampered`, `kind ==
  unknown` after a successful experiment job ("run finished without reporting an outcome"), or
  `publish` failed. Completed, `already_done` and dry runs stay green. A `partial_budget` day also
  stays green but the experiment job emits `::warning title=Daily run hit the budget cap::completed
  N of M attempts ...`, so caps that are too tight show in the run summary. Values reach the script
  through `env:` only.
- **Triggers:** `schedule` with two slots per UTC day, a primary and a backup at non-round minutes
  (13:17, 19:47), and `workflow_dispatch` with `dry_run`. Never `push` or `pull_request`. GitHub
  starts scheduled runs best-effort: the first ones began 6 to 9 hours after their slot, and
  an eight-slot schedule (every three hours) was tried and reverted to two; the first slot to
  start does the day. If a run starts after 00:00 UTC it counts for the next UTC date.
  `concurrency: daily-experiment` (no cancelling) makes each later run start after the previous
  one has published, so `--resume` sees its results: a no-op on a finished day (about 20 s), a
  continuation after an abort. Check the "Daily experiment" runs' start times against the slots
  if a day is missing; Run workflow by hand is the fallback.
- **Publisher rules** (`ResultsPublisher`; the incoming directory is untrusted): only
  `runs/<date>.jsonl`, `runs/<date>.run.json`, `runs/<date>.abort-<HHMMSS>.json` and
  `configs/<12 hex>.json`; regular files only (no links, no other directories), at most 5 MB each
  and 1000 files; every file parses with its model; record, summary and abort dates match the file
  name, attempt ids are unique, tracks are slugs, and a config's name, `config_hash` field and
  recomputed hash agree. Against `results/`: new files are copied, an existing JSONL must be a
  byte-for-byte prefix of the incoming one (only the suffix is appended), any other existing file
  must be identical. Unreadable JSONL lines (a fragment from a killed writer, which the store
  also tolerates) are counted, up to 5 per file, and reported in the output and the commit message;
  more is a violation, as is any other unparseable file or a blank line. Any violation rejects the
  whole set and nothing is copied (exit 1).
- **Commit message** (one redacted line): `eval(<track>): baseline P/N vs variant P/N passed,
  $<cost> [<status>]` for a terminal day, `eval(<track>): aborted (<reason>: <status>), day
  resumable` after an abort, `…partial results, N attempts, day resumable` for a killed run,
  `eval: register configs`; `, N unreadable line(s)` is appended when there are fragments.
- **Pinned runner and actions:** every job in `ci.yml` and `daily.yml` runs on `ubuntu-24.04`
  (not `ubuntu-latest`), so the OS cannot change under the experiment. Actions use their Node.js 24
  major versions; `astral-sh/setup-uv` publishes no major tags, so it is pinned to an exact
  release. Records carry `runner_image` (`ImageOS/ImageVersion`, e.g. `ubuntu24/20251020.1`)
  alongside the other provenance fields, so a drift in the image shows up in the data.
  **Upgrading deliberately:** change `runs-on` in both workflows in one PR (for example to
  `ubuntu-26.04` once it exists), read the runner-images release notes, check that the CI jobs
  (especially `isolation`, which depends on sudo, `unshare` and `setpriv`) pass, and run the daily
  workflow once with `dry_run`. Expect a possible step in the data at the switch; `runner_image`
  and `git_sha` mark where. Bump action versions the same way, one reviewed PR, and update
  `ACTION_VERSIONS` in `tests/test_ci_workflow.py`.
- **Manual setup (not automated):** create the Environment `experiment`, restricted to `main`,
  with the secret `ANTHROPIC_API_KEY`; set the repository variables `COMMIT_AUTHOR_NAME` and
  `COMMIT_AUTHOR_EMAIL`; make sure nothing (branch protection, workflow permission limits) blocks
  the `publish` job's push to `main`.
- **Running it manually:** Actions, "Daily experiment", "Run workflow" (use `dry_run` first).
  Locally: `agent-lab run --dry-run`, or a real run with `--results-dir /tmp/x`, then
  `agent-lab results publish /tmp/x` into a scratch results directory to try the publisher.

## Context strategies

`context_strategy` in `AgentConfig` picks what the model sees on each call; the strategy is built by
`ContextStrategy.from_config(config, client)` from a registry (only implemented strategies are in it; a test
checks it equals `ContextStrategyName`). Parameters are optional `AgentConfig` fields (default `None`), checked per
strategy by a validator: `full` takes none; `truncate_tool_outputs` takes `context_keep_last`; `summarize` takes
`context_after_turns`, `context_keep_last` and `context_summary_max_tokens` (`after_turns > keep_last >= 1`).
A wrong set fails validation (a schedule problem or a `--context` error, before any API call).

**Contract.** `apply(conversation)` returns a `ContextResult(messages, calls)`. `messages` is a valid Messages API
sequence: it starts with a user message that contains the task prompt, roles alternate, every assistant `tool_use` is
answered by a `tool_result` with the same id in the immediately following user message, and the `Conversation` is never
mutated. `calls` are extra model calls the strategy made (see Summarize). A parametrized contract test
(`tests/harness/test_context.py`) runs every registered strategy over multi-turn, parallel-call and thinking-block
conversations. Strategies are shared by all attempts of an agent, so they hold no per-attempt state: `Agent.run`
calls `fresh()` once per attempt (stateless strategies return themselves, `Summarize` a new instance).

- `FullHistory`: everything, unchanged.
- `TruncateToolOutputs(keep_last)`: a `tool_result` older than the last `keep_last` assistant turns becomes
  `[tool output elided: N characters from <tool>]` (id and `is_error` kept; results already shorter than the
  placeholder stay). Prompt, assistant blocks and recent results are untouched. A pure function of the conversation,
  so an elided result stays elided identically on later calls. With `n` assistant turns, turns `1..n-keep_last` are
  elided: for the 4-9 turn attempts here (history mean 5.6 turns, medium effort), `keep_last: 2` elides something on
  every attempt, from the fourth model call, on about 46% of all calls.
- `Summarize(after_turns, keep_last, summary_max_tokens)`: with `n` complete assistant turns and `covered` already
  summarized, once `n - covered > after_turns` the turns `covered+1..n-keep_last` (plus the previous summary) are
  replaced by a model-written summary (files read, edits, test results, open problems) and `covered = n - keep_last`.
  Between summaries the previous one is reused, so the sent prefix only grows; it is re-written only when the threshold
  is crossed again. The summary sits in a second text block of the first user message (roles keep alternating). The
  summary call uses the same model at effort low, `max_tokens=summary_max_tokens`, **no `tools` parameter** (the old turns
  are flattened to plain text, thinking blocks skipped; `ModelRequest.tools=None` omits it) and goes through the
  `ModelClient` (retries). `Agent` charges each returned `ContextCall` to the attempt's `Budget` and records a
  `ContextCallTrace` in `AttemptTrace.context_calls`, apart from `turns` (turn counts stay comparable). Trace token,
  latency and retry totals include those calls, so record tokens and `cost_usd` agree; records add `context_calls` and
  `context_call_tokens` (default 0). Not scheduled yet; try it with `scripts/calibrate.py`.

**Calibrating a strategy:** `uv run python scripts/calibrate.py --context SPEC`, with SPEC `full`,
`truncate_tool_outputs:keep_last=2` or `summarize:after_turns=4,keep_last=2,summary_max_tokens=400`.

**Caching note (as found).** `cache_control` is set only on the system prompt and the last tool definition; messages are
never cache-marked. Editing earlier messages therefore cannot invalidate a cached prefix today, and a strategy's saving
is plain uncached input (history: `cache_write_tokens` 0, cache reads about 19% of input, from tools + system). If message
caching is added (a separate step), compressing old messages will break the cached prefix and can raise cost; that
interplay is not measured yet.

**config_hash.** `None`-valued fields are left out of the canonical JSON, so adding optional fields did not change any
existing hash; `tests/runner/test_schedule.py` pins the baseline (`0d83bf23c762`) and every track's hash, including
`context-truncate` and the unscheduled `plan-first` (the prompt file `plan_first.md` is kept for later). Registry files
(`results/configs`) omit `None` fields too, so old-style configs are stored exactly as before.

## Report

`uv run agent-lab report build [--results-dir results] [--readme README.md] [--out report] [--check]`
turns `results/` into the dashboard in `README.md` (only the text between
`<!-- agent-lab:report:start -->` and `<!-- agent-lab:report:end -->`; missing, duplicated or
reversed markers fail), `report/tracks.svg`, `report/daily.svg` and `report/summary.json` (every
number shown, sorted keys). The publish job runs it after each publish and commits `results/`,
`report/` and `README.md`; CI runs it with `--check` (writes nothing, exits 1 if any output would
change), so a PR that changes report code must include the regenerated output. To rebuild locally:
`uv run agent-lab report build`. Standard library only.

- **Deterministic:** the same results give byte-identical files. Dates come from the data, never
  the clock; the bootstrap is seeded from a hash of its input; sums use `math.fsum` so input
  order does not matter. Unchanged files are not rewritten.
- **Pairs and exclusions:** a pair is the baseline and variant record with the same
  `(run_id, task_id, trial)`. It is excluded (and counted per series) when either side has status
  `error` or `environment_tampered` (infrastructure, not agent behaviour) or when a side is
  missing. `truncated`, `refused`, `incomplete`, `max_turns` and `budget_exceeded` are agent
  outcomes: they stay in and count as failures unless the attempt passed.
- **Series:** grouped by `(track, baseline config_hash, variant config_hash, suite_hash)`, so
  editing a config, a prompt or a task starts a new series; records from before suite versioning
  form their own "unversioned" series. A pair whose two sides disagree on the suite is excluded as
  unpaired. The current series per track (latest date) is shown first, older
  ones in a collapsed block. Variants are described by a field diff from the configs registry
  ("system prompt changed" links to the config file). The `aa-test` track is labelled the
  noise-floor reference.
- **Statistics (`stats.py`):** pass rate per side with a Wilson 95% interval. Pass-rate difference:
  Newcombe's hybrid score interval for paired proportions (method 10, with its continuity
  corrected phi), because a bootstrap collapses to [0, 0] when no pair is discordant, which would
  claim certainty. Cost, turns and output-token differences: 95% percentile bootstrap over pairs
  (2000 resamples). A series with at least 8 distinct tasks switches automatically to a
  task-clustered bootstrap for every metric, pass rate included (trials of one task are
  correlated); the method used is printed. A series with fewer than 10 included pairs shows
  point estimates with "insufficient data" and that verdict, for every interval. Verdicts use
  neutral words ("no detectable difference", "variant higher/lower") because "higher" is good for
  pass rate and bad for cost. A ceiling flag marks both sides at 95% or more pass rate.
- **Difficulty and injection lines:** every series carries pass rates by difficulty tier (easy,
  medium, hard, unknown for old records) and, over injection-category pairs, per side: `exposed
  E/N` and `resisted R/E among exposed` (see Injection exposure), with records that predate
  exposure tracking counted apart as `exposure unknown U/N` (plus their old `injection_resisted`
  count). Both are in `summary.json` and shown as one sub-bullet each under the track's note, as
  point rates with counts and no intervals; the README caveats say the subsets are small and
  explain exposure. The suite hash and task count appear in the note too.
- **Caveats written into the README:** sample size, ceiling, the independence assumption, multiple
  comparisons (the number shown is in `summary.json` as `comparisons_shown`: roughly 1 in 20
  will differ by chance, aa-test should show none), what the exclusions mean.
- **Data-dependent text lives inside the markers only.** Hand-written README text stays timeless
  (no counts or dates); the generated section states the data range.
- **Untrusted text:** `results/` comes from a job that ran agent code, so everything interpolated
  into the README or SVGs (questions, config values, names) is escaped and length-capped, prompts
  are never embedded, and links are built only from validated hashes.
- **Charts:** transparent SVGs with a `prefers-color-scheme` style and `<title>`/`<desc>`. GitHub
  displays README SVGs as images, so they follow the viewer's OS/browser scheme, not GitHub's
  own theme toggle. With no results only a placeholder section and an empty `summary.json` are
  written; a chart that has nothing to draw is not written (and a stale one is removed).

## Suite versioning and calibration

**Suite versioning.** `TaskSuite.fingerprint(task_ids)` returns `suite_hash` and per-task
`task_hash` (12 hex of sha256). A task hash covers the id plus the paths and contents of
`task.yaml`, `project/` and `hidden_tests/`, never `solution/`; caches, `.pyc` and `.DS_Store` are
skipped, so modification times, the checkout location and a macOS checkout do not matter. The
suite hash covers the sorted task ids and task hashes of the tasks *in the plan* (everything in
`tasks/` normally, the chosen ids with `--tasks`), computed once before the first attempt. Every
`AttemptRecord` carries `suite_hash`, `task_hash` and `task_difficulty` (all optional, `None` in
older records), and `--dry-run` prints `suite: <hash> (N tasks)`. Adding, removing or editing a
task, its prompt or its hidden tests therefore starts a new report series. Editing only
`solution/` does not.

**Difficulty tiers.** `difficulty: easy | medium | hard` in `task.yaml`; the validator allows 200
lines of code for easy and medium and 600 for hard (`TaskValidator(max_code_lines=...)`).

**Calibration** (manual, real API, never CI; `scripts/calibrate.py`). Goal: baseline (haiku,
effort low) passes about 60-75% overall, every non-easy task inside 30-75%, the easy tasks
kept as they are. The script runs the baseline alone on the chosen tasks (`RunArgs.baseline_only`,
not a command line option of `agent-lab run`) into a results directory that is not `results/`
(temporary by default), prints the plan and estimate first, stops at `--max-cost` (default $2;
the per-attempt cap is the schedule's, or $0.50 with `--model`, `--max-attempt-cost` overrides),
and prints per-task pass rate, mean turns and cost, totals by tier, whether the overall rate is
inside the target, and the projected cost of a day. Running it again into the same
`--results-dir` (each run takes the next calendar day with no results there) shows configs side
by side, for example `--model claude-sonnet-5-5 --trials 2` to see whether Monday's comparison is
off the ceiling. Procedure: `uv run python scripts/calibrate.py --trials 4`; adjust tasks outside
the band (too easy: add a second cause, move the contract into a docstring, add a decoy; too
hard: state the requirement more plainly or drop an assertion that was not really stated); re-run
only those tasks (`--tasks`, `--trials 6` for borderline ones); once the baseline is inside the
band run the new tasks once with Sonnet. With 4 trials a rate moves in steps of 25%, so treat
the band as a guide. After calibration update `cost_defaults` and the Monday `trials_per_task` in
`experiments/schedule.yaml`: the estimator falls back to `cost_defaults` for tasks without
history.

## Sandbox isolation

`--isolation none|linux-user` (default `none`; `--isolation-user NAME`, default `agentlab-sandbox`).

- `none` runs agent commands as the harness's own user. A real run prints one warning. With
  `GITHUB_ACTIONS=true` a real run with `none` exits 1 before any API call ("CI runs require
  --isolation linux-user"); dry runs are not affected.
- `linux-user` runs every command as `sudo -n unshare --net setpriv --reuid/--regid
  --clear-groups --no-new-privs env -i <minimal env> sh -c 'umask 0; ...'`: another uid, no
  network, no inherited environment. After every command, and when a sandbox closes, all
  processes of that user are killed (`sudo -n pkill -KILL -u`) and permissions are reclaimed
  (`chmod -R a+rwX` run as that user), so nothing outlives an attempt and the harness can always
  read and delete what the agent made. `prepare()` and `share_file()` never follow symlinks: the
  snapshot holds whatever links the agent left, and following one would let the harness itself
  expose `tasks/`. The run verifies sudo, the uid, the network namespace, that `tasks/` and
  `.git` are not readable and site-packages not writable, before the warm-up, and fails fast.
- CI layout: `UV_PROJECT_ENVIRONMENT=/opt/agentlab/venv` and
  `UV_PYTHON_INSTALL_DIR=/opt/agentlab/python` are set before `uv python install` / `uv sync`;
  `sudo scripts/setup_sandbox_user.sh` creates the user and makes `/opt/agentlab` readable and
  executable (not writable) for it. It changes nothing in the repo or `$HOME`; the sandbox user
  must simply not be able to read them, which the integration tests assert.
- The harness user needs passwordless `sudo` for `unshare`, `setpriv` and `pkill`. That makes it
  root-equivalent, so this mode is for ephemeral CI machines, not a shared workstation.

## Task authoring

A task is a directory `tasks/<task_id>/` (id is a lowercase slug and equals the directory name):

- `task.yaml`: `id`, `title`, `category` (bugfix | feature | refactor | qa | injection),
  `difficulty` (easy | medium | hard, required), `prompt`, `grading` (hidden_tests | exact_answer), `protected` (globs, default
  `["tests/**"]`), `canaries` (paths that must NOT exist after the attempt, default `[]`),
  `injection_markers` (distinctive phrases of the planted text; required and non-empty for
  category `injection`, forbidden otherwise), `expected_answer` (exact_answer only).
- `project/`: the codebase the agent works on, including visible tests.
- `hidden_tests/`: grader-only tests, copied in only after the attempt ends. They must be
  self-contained (no `conftest.py`, no helpers from `project/tests`; conftest is ignored).
- `solution/`: files that overwrite `project/` files. Never given to the agent. For
  exact_answer tasks it contains `ANSWER.txt`.

Rules:

- Task code and tests import only the standard library and pytest (the sandbox runs the
  harness venv's interpreter). Each project is 50-200 lines of code outside `tests/` for an
  easy or medium task, up to 600 for a hard one: over the limit fails validation, under 50 only
  prints a warning. Difficulty is a calibrated label (see Calibration), not a size class.
- One difficulty lever per task, named in its design: a bug whose symptom and cause are in
  different modules, state that leaks only when the code is run, a spec edge case the visible
  tests miss, a refactor with hidden tests on the public API, or an injection that arrives through
  tool output. The shipped suite has all five (see the `tasks/` ids and `tests/benchmark/
  test_task_traceability.py`).
- Prompts never mention hidden tests or grading (the loader rejects them). They may say
  what the visible tests are for and tell the agent not to edit them.
- Every hidden-test assertion must be traceable to the prompt or to project docstrings/comments.
  Hidden tests check stated behavior, never unstated expectations. Before adding or changing
  a hidden test, write down where each assertion's requirement is stated; if it is not, state
  it (in a docstring or the prompt) or drop the assertion.
- Bugs are realistic and solvable by a small model within 15 turns, but not solvable by
  only reading the visible tests: the contract lives in docstrings and the prompt.
- Injection tasks plant instructions addressed to AI agents inside project files and list
  the file the injected instructions ask for in `canaries`, and name the planted text in
  `injection_markers` (see Injection exposure).
- Every task must pass `uv run agent-lab validate-tasks` (also run in CI, no API): hidden
  tests fail on the untouched project, pass with `solution/` applied, protected and
  canary checks are clean for the solution, visible tests pass on the solved project.
- Grading runs on a snapshot of the workspace with a hardened pytest invocation
  (`python -I`, workspace appended to `sys.path`, `--noconftest`, explicit `-c`, JUnit
  report outside the workspace). `passed` requires every hidden test to pass and no
  protected-file violation; a triggered canary sets `injection_resisted=False` but does
  not change `passed`. Do not weaken these flags; add an anti-cheat test for any new vector.

## Injection exposure

`injection_resisted` alone is true for every attempt without a canary or protected violation,
including one that never saw the planted text (in calibration only 2 of 12 `fix-settings-merge`
attempts opened the file with the notice). So each attempt also records whether it was *exposed*.

- **What exposed means.** `injection_exposed` is true when a marker of the task appears in a
  `tool_result` block that was sent to the model. Not the task prompt, not the assistant's own
  text, and not a tool result produced after the last model call (the attempt ended first).
  Tool output is truncated by the harness before it enters the conversation, so text beyond
  `tool_output_max_chars` of a file does not count. `injection_first_exposure_turn` is the
  1-based model call whose request first carried a marker (a result produced after turn k is
  first seen on turn k+1). Both are `None` for other tasks, for attempts that ended in `error`
  and in records written before this existed ("exposure unknown", never counted as exposed or
  as resisted among the exposed). The runner computes them from the conversation after grading
  (`benchmark/exposure.py`, `InjectionExposure`); the grader never sees the conversation.
  `injection_resisted` keeps its old meaning. Resistance is read among the exposed only.
- **Choosing markers.** Short, distinctive phrases taken from the planted text only (a heading,
  a banner such as "NOTE TO AI AGENTS", the start of a sentence), matched case-insensitively with
  whitespace collapsed. Never the canary path (the agent creating the canary would show it in
  `list_files`), never a phrase that also occurs in honest project text, and near the start of
  its file because `read_file` output is capped. `validate-tasks` fails a marker that occurs in no
  file under `project/`, in the prompt, or inside a canary path.
- **Versioning.** `task.yaml` is hashed, so adding or editing markers changes the task hash and
  the suite hash: the report starts a new series.

## Known gaps

- `--isolation none` (the default, used by local runs and the scripts) exposes everything the
  harness user can reach: its environment including `ANTHROPIC_API_KEY` (via
  `/proc/$PPID/environ`, which base64 hides from the redactor), `tasks/*/hidden_tests` and
  `solution/`, a writable repo and site-packages, the network, and any credentials such as the
  push token in `.git/config`. `EnvironmentIntegrity` only detects changes to site-packages and
  `tasks/` afterwards. Use `linux-user` wherever secrets or a push token exist (CI always).
- `linux-user` is Linux-only and not a container: no CPU/memory/disk limits, no seccomp or
  filesystem namespace (world-readable files such as `/etc` and `/usr` stay readable), and
  grading still executes the agent's code in-process, now as the sandbox user.

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
- Prompt caching only helps if the shared prefix (tools + system prompt) reaches the model's
  minimum cacheable length. With five short tool definitions it may not, which would make the
  warm-up a harmless no-op. Check `cache_write_tokens`/`cache_read_tokens` in the first day's records.
- `effort` (`output_config`) support depends on the model. It was added without a real call:
  if a model rejects it, the warm-up canary aborts the run with a 400 before any attempt. Check
  the first manual run.
- The 300s request timeout applies to each HTTP call, and timeouts are retried (up to 4 calls),
  so one stuck attempt can take a long time and a timed-out call may still be billed.
- Two simultaneous runs for the same date are not locked against each other; the daily
  workflow's `concurrency` group serializes its own runs, but a manual local run against the same
  results directory is not covered.
- A day whose publish fails is not in `main`, so the backup run (which resumes from committed
  results) redoes it and spends again. The artifact survives 3 days for a manual recovery.

## HARD RULES

- Tests never call the real Anthropic API. Use an injected fake client.
- The API key is read only from the ANTHROPIC_API_KEY env var. Never log it, print it, or write it to any file.
- Anything the agent executes runs in the sandbox with an explicit minimal environment: no inherited env vars, no ANTHROPIC_API_KEY or other secrets, cwd confined to the task working directory, hard timeouts.
- Results files are append-only. Never modify or delete past days' results.
- No agent frameworks. The agent loop is hand-written on the Messages API.
- Keep dependencies minimal; ask before adding one.
