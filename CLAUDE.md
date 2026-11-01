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
  - `context.py` — `ContextStrategy` ABC and implementations
  - `tools/` — `Tool` ABC, file/exec tools, and `ToolRegistry`
  - `sandbox.py` — sandboxed execution with minimal explicit environment
  - `isolation.py` — `CommandIsolation` ABC, `NoIsolation`, `LinuxUserIsolation` (commands as a
    dedicated user, no network, processes killed after every command)
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
  - `environment.py` — `EnvironmentIntegrity`: detects site-packages and `tasks/` changes between attempts
  - `validation.py` — `TaskValidator`: checks a task is solvable and not already solved
- `src/agent_lab/runner/`
  - `schedule.py` — `ExperimentSchedule`: validated baseline, weekday tracks, `config_hash`
  - `plan.py` — `RunPlanner`: deterministic, interleaved `RunPlan` for a date
  - `estimator.py` — `CostEstimator`: history-based cost estimates with per-model defaults
  - `warmup.py` — `CacheWarmer`: one cache warm-up call per distinct config (also the canary)
  - `budget.py` — `DailyBudget`: the day's spending cap
  - `experiment.py` — `ExperimentRunner`: runs a plan, records each attempt immediately
  - `records.py` — `AttemptRecord`, `RunSummary`, `AbortRecord`, `RecordBuilder`
  - `provenance.py` — `ProvenanceReader`: git sha, lockfile hash, versions, ci|local
  - `storage.py` — `ResultsStore` (append-only results) and `TranscriptStore`
  - `database.py` — `ResultsDatabase`: derived SQLite database (never committed)
  - `wiring.py` — `AgentFactory`: client (`max_retries=0`, 300s request timeout), prices,
    redactor; shared with scripts
  - `publish.py` — `ResultsPublisher` (validates an untrusted incoming directory, then copies it
    into `results/`) and `CommitMessageBuilder`
  - `run_command.py` — `RunCommand`, `DbCommand`, `ResultsCommand`: the logic behind the CLI commands
  - `cli.py` — `agent-lab` entry point (`validate-tasks`, `run`, `db rebuild`,
    `results publish|commit-message`)
- `src/agent_lab/report/build.py` — report generation
- `.github/workflows/` — `ci.yml` (checks, isolation tests) and `daily.yml` (the daily run)
- `tasks/` — task fixtures (see Task authoring)
- `experiments/schedule.yaml` — baseline, defaults and one track per ISO weekday
- `experiments/prompts/` — system prompts referenced by the schedule
- `config/pricing.yaml` — model pricing data
- `results/runs/` — committed results: `<date>.jsonl`, `<date>.run.json`, `<date>.abort-*.json`
- `results/configs/<config_hash>.json` — committed registry of every resolved config that ran
  (prompt text included, `name` excluded: exactly what the hash covers)
- `tests/` — pytest tests
- `scripts/` — manual scripts: `smoke.py`, `run_task.py` (real API; never run in CI) and
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
[--results-dir DIR] [--transcripts-dir DIR] [--resume] [--dry-run]`. The API key comes from
the `ANTHROPIC_API_KEY` environment variable only. `--dry-run` prints the track, question,
resolved configs with their `config_hash`, the ordered plan and the cost estimate, makes no
API calls and writes no files (no key needed).

**Who writes `results/`:** only CI, and only through the daily workflow's `publish` job (see
Daily workflow). Local runs always use `--results-dir /tmp/somewhere` so they never pollute the
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
   readable abort files belong to another run id or track, or if a role's `config_hash` differs
   from the one already recorded (the schedule or a prompt was edited since): resuming would
   mix configurations in one day. `--trials` must be at least 1; `--tasks` must have no empty
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
   or an attempt), a warm-up failure, or 3 consecutive attempts with `status == "error"`
   (failed grades never count) write `<date>.abort-<HHMMSS>.json` (exclusive create; the
   summary fields plus `reason`, `error_type`, `error_status`, completed ids and
   `skipped_attempts`) and do NOT write `.run.json`, so the day stays resumable.
8. Exit 0 whenever a record was written or the day was already done; nonzero only for
   configuration errors or crashes before any record could be written.

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
It has two jobs so that the job that executes agent code can never push:

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
  runs `agent-lab results publish`, and if `results/` changed commits only `results/` (author and
  committer from the repository variables `COMMIT_AUTHOR_NAME` / `COMMIT_AUTHOR_EMAIL`, which
  must be set), with the message from `agent-lab results commit-message`, then pushes; on a
  rejected push it `pull --rebase`s and retries, three pushes at most.
- **Triggers:** `schedule` twice per UTC day (13:17 and 19:47) and `workflow_dispatch` with
  `dry_run`. Never `push` or `pull_request`. `concurrency: daily-experiment` (no cancelling) makes
  the backup run start after the primary has published, so `--resume` sees its results: a
  no-op on a finished day, a continuation after an abort.
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
  harness venv's interpreter). Each project is 50-200 lines of code outside `tests/`:
  over 200 fails validation, under 50 only prints a warning.
- Prompts never mention hidden tests or grading (the loader rejects them). They may say
  what the visible tests are for and tell the agent not to edit them.
- Every hidden-test assertion must be traceable to the prompt or to project docstrings/comments.
  Hidden tests check stated behavior, never unstated expectations. Before adding or changing
  a hidden test, write down where each assertion's requirement is stated; if it is not, state
  it (in a docstring or the prompt) or drop the assertion.
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

- `--isolation none` (the default, used by local runs and the scripts) exposes everything the
  harness user can reach: its environment including `ANTHROPIC_API_KEY` (via
  `/proc/$PPID/environ`, which base64 hides from the redactor), `tasks/*/hidden_tests` and
  `solution/`, a writable repo and site-packages, the network, and any credentials such as the
  push token in `.git/config`. `EnvironmentIntegrity` only detects changes to site-packages and
  `tasks/` afterwards. Use `linux-user` wherever secrets or a push token exist (CI always).
- `linux-user` is Linux-only and not a container: no CPU/memory/disk limits, no seccomp or
  filesystem namespace (world-readable files such as `/etc` and `/usr` stay readable), and
  grading still executes the agent's code in-process, now as the sandbox user.

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
