# agent-lab

An LLM agent harness plus a daily automated experiment. Each day a
scheduled GitHub Actions workflow runs the harness (built directly on the
Anthropic Messages API, no agent frameworks) against a fixed suite of small
Python coding tasks, compares a baseline agent config with one variant, grades
attempts with hidden pytest tests, and commits the results and an updated report.

## How it runs

A scheduled GitHub Actions workflow (`.github/workflows/daily.yml`) runs the experiment from
eight slots a day (GitHub starts scheduled runs late); every run after the first is a no-op once
the day is finished. Each weekday has
one question (for example "does the structured tool set beat a single bash tool?"): the harness
runs a baseline agent configuration and one variant on the same small Python tasks, several
trials each, and grades every attempt with hidden pytest tests. Agent commands run as a separate,
network-less Linux user, and the job that runs them cannot push: a second job validates the
results and commits them.

Results are committed under `results/` and are append-only: `results/runs/<date>.jsonl` (one line
per attempt), `<date>.run.json` (the day's summary), `<date>.abort-*.json` (abort events: the run
stopped and the day stays resumable) and `results/configs/<hash>.json` (every configuration that
ran, prompt included).
`agent-lab db rebuild` turns them into a local SQLite database for ad-hoc queries.

**Status:** the harness, tasks, runner, isolation, daily workflow and report are implemented and
tested. The dashboard below is generated from the committed results and updated by the daily
workflow; it states the data range it covers.

<!-- agent-lab:report:start -->
## Results

Data through **2026-11-08** · 8 day(s) run · 275 attempts · **$1.1560** total spend (attempts $1.1511 + warm-ups $0.0049).

Latest day: eval\(aa-test\): aborted \(warmup\_failed: 400\), day resumable

| Track | Pairs (excl.) | Baseline pass | Variant pass | Δ pass (pp) | Δ cost / attempt | Δ turns | Verdict | Flags |
|---|---|---|---|---|---|---|---|---|
| aa-test | 12 (2) | 83% [55–95] | 92% [65–99] | +8.3 [+0.0, +30.0] | -$0.0003 [-$0.0009, +$0.0002] | -0.9 [-1.7, -0.3] | pass: no detectable difference; cost: no detectable difference | noise-floor reference |
| model-comparison | 12 (0) | 100% [76–100] | 100% [76–100] | +0.0 [-24.2, +24.2] | +$0.0176 [+$0.0159, +$0.0196] | -3.0 [-4.0, -2.1] | pass: no detectable difference; cost: variant higher | ceiling |
| plan-first | 12 (0) | 100% [76–100] | 100% [76–100] | +0.0 [-24.2, +24.2] | +$0.0001 [-$0.0004, +$0.0004] | -0.2 [-1.1, +0.4] | pass: no detectable difference; cost: no detectable difference | ceiling |
| self-verify | 32 (0) | 91% [76–97] | 88% [72–95] | -3.1 [-9.4, +0.0] | +$0.0013 [+$0.0004, +$0.0025] | +0.8 [+0.2, +1.3] | pass: no detectable difference; cost: variant higher |  |
| tool-design | 32 (0) | 94% [80–98] | 75% [58–87] | -18.8 [-37.5, +0.0] | +$0.0008 [-$0.0001, +$0.0018] | +0.9 [-0.5, +2.3] | pass: no detectable difference; cost: no detectable difference |  |
| turn-budget | 12 (0) | 100% [76–100] | 83% [55–95] | -16.7 [-44.8, +10.4] | -$0.0004 [-$0.0009, +$0.0000] | -0.9 [-1.8, -0.1] | pass: no detectable difference; cost: variant lower |  |

- **aa-test**: How large is the day-to-day noise when both configurations are identical? Variant vs baseline: identical configs. Cost per solved task (baseline → variant): $0.0044 → $0.0037. Suite `37e677db05e7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 100% → 100% (6 pairs); medium 75% → 100% (4 pairs); hard 50% → 50% (2 pairs)
  - Injection tasks resisted (baseline → variant): 3/3 → 3/3 (100% → 100%)
- **model-comparison**: Does claude-sonnet-5-5 solve more tasks than claude-haiku-5-5, and at what cost? Variant vs baseline: model: claude-haiku-5-5 → claude-sonnet-5-5. Cost per solved task (baseline → variant): $0.0028 → $0.0203. Suite unversioned (3 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): unknown 100% → 100% (12 pairs)
  - Injection tasks resisted (baseline → variant): 4/4 → 4/4 (100% → 100%)
- **plan-first**: Does asking the agent to write a plan before acting improve results? Variant vs baseline: [system prompt changed](results/configs/f155db74474c.json). Cost per solved task (baseline → variant): $0.0030 → $0.0031. Suite unversioned (3 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): unknown 100% → 100% (12 pairs)
  - Injection tasks resisted (baseline → variant): 4/4 → 4/4 (100% → 100%)
- **self-verify**: Does requiring the agent to run tests and re-read its changes improve results? Variant vs baseline: [system prompt changed](results/configs/55d2bd0df8d1.json). Cost per solved task (baseline → variant): $0.0042 → $0.0058. Suite `37e677db05e7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 100% → 100% (12 pairs); medium 100% → 92% (12 pairs); hard 62% → 62% (8 pairs)
  - Injection tasks resisted (baseline → variant): 8/8 → 8/8 (100% → 100%)
- **tool-design**: Do the structured tools beat a single bash tool? Variant vs baseline: tool\_set: structured → bash\_only. Cost per solved task (baseline → variant): $0.0037 → $0.0056. Suite `37e677db05e7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 100% → 100% (12 pairs); medium 92% → 83% (12 pairs); hard 88% → 25% (8 pairs)
  - Injection tasks resisted (baseline → variant): 8/8 → 8/8 (100% → 100%)
- **turn-budget**: How much does cutting the turn budget from 15 to 6 hurt success? Variant vs baseline: max\_turns: 15 → 6. Cost per solved task (baseline → variant): $0.0028 → $0.0029. Suite unversioned (3 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): unknown 100% → 83% (12 pairs)
  - Injection tasks resisted (baseline → variant): 4/4 → 4/4 (100% → 100%)

![Variant minus baseline by track](report/tracks.svg)

![Daily spend](report/daily.svg)

<details><summary>Older series (2): a config or prompt changed</summary>

| Track | Pairs (excl.) | Baseline pass | Variant pass | Δ pass (pp) | Δ cost / attempt | Δ turns | Verdict | Flags |
|---|---|---|---|---|---|---|---|---|
| aa-test (2026-11-01 → 2026-11-01, suite unversioned) | 12 (0) | 100% [76–100] | 100% [76–100] | +0.0 [-24.2, +24.2] | +$0.0000 [-$0.0003, +$0.0003] | +0.1 [-0.7, +0.8] | pass: no detectable difference; cost: no detectable difference | ceiling, noise-floor reference |
| tool-design (2026-11-03 → 2026-11-03, suite unversioned) | 12 (0) | 100% [76–100] | 92% [65–99] | -8.3 [-35.4, +16.9] | -$0.0006 [-$0.0009, -$0.0003] | -1.8 [-2.3, -1.3] | pass: no detectable difference; cost: variant lower |  |

</details>

**Caveats**

- Deltas are variant minus baseline. "higher" and "lower" are neutral words: more passes is good, more cost is not. Intervals are 95%.
- Sample size: a series with fewer than 10 included pairs shows point estimates only ("insufficient data"). Intervals are wide until a series has many days of data.
- Ceiling: when both sides pass at least 95% of attempts, a pass-rate comparison cannot show an improvement; look at cost and turns instead.
- Independence: trials of the same task are correlated, so treating pairs as independent understates uncertainty with few tasks. A task-clustered bootstrap is used automatically for a series with at least 8 tasks.
- Multiple comparisons: 24 comparisons are shown (cost: 8, pass: 8, turns: 8), re-evaluated daily. At 95%, roughly 1 in 20 will show a difference by chance. The aa-test row compares identical configurations: it is the noise-floor reference and should show none.
- Subsets: the per-difficulty and injection lines are point rates over small subsets of the pairs, with no intervals; do not read a difference between tiers as significant.
- Exclusions: a pair is left out when either attempt ended in `error` (an API or harness failure), was environment-tampered, or its other side never ran; the number is in parentheses. Truncated, refused, incomplete, max-turn and over-budget outcomes are agent outcomes: they stay in and count as failures unless the attempt passed.
- Method: pass rate: Newcombe hybrid score interval \(paired, method 10\); cost, turns, output tokens: pair percentile bootstrap; task-clustered percentile bootstrap for every metric, pass rate included.
- Charts are SVGs that follow your OS/browser colour scheme, which can differ from GitHub's own theme setting.
<!-- agent-lab:report:end -->

## License

MIT
