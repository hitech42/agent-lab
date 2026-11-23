# agent-lab

An LLM agent harness plus a daily automated experiment. Each day a
scheduled GitHub Actions workflow runs the harness (built directly on the
Anthropic Messages API, no agent frameworks) against a fixed suite of small
Python coding tasks, compares a baseline agent config with one variant, grades
attempts with hidden pytest tests, and commits the results and an updated report.

## How it runs

A scheduled GitHub Actions workflow (`.github/workflows/daily.yml`) runs the experiment from
two slots a day (GitHub starts scheduled runs late); the second run is a no-op once
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

Data through **2026-11-23** · 23 day(s) run · 1223 attempts · **$6.3541** total spend (attempts $6.3107 + warm-ups $0.0189 + triage $0.0245).

Latest day: eval\(model-comparison\): aborted \(consecutive\_errors: 400\), day resumable

| Track | Pairs (excl.) | Baseline pass | Variant pass | Δ pass (pp) | Δ cost / attempt | Δ turns | Verdict | Flags |
|---|---|---|---|---|---|---|---|---|
| aa-test | 64 (0) | 78% [67–86] | 78% [67–86] | +0.0 [-9.4, +9.4] | -$0.0002 [-$0.0004, +$0.0000] | +0.1 [-0.3, +0.5] | pass: no detectable difference; cost: variant lower | noise-floor reference |
| context-truncate | 32 (0) | 81% [65–91] | 75% [58–87] | -6.2 [-37.5, +18.8] | +$0.0008 [+$0.0004, +$0.0015] | +1.9 [+0.8, +3.6] | pass: no detectable difference; cost: variant higher |  |
| effort-sweep | 32 (0) | 75% [58–87] | 88% [72–95] | +12.5 [+3.1, +25.0] | +$0.0010 [+$0.0003, +$0.0021] | +1.2 [+0.8, +1.5] | pass: variant higher; cost: variant higher |  |
| model-comparison | 38 (2) | 74% [58–85] | 100% [91–100] | +26.3 [+5.3, +52.6] | +$0.0232 [+$0.0141, +$0.0347] | -0.7 [-1.6, +0.1] | pass: variant higher; cost: variant higher |  |
| plan-first | 32 (0) | 91% [76–97] | 91% [76–97] | +0.0 [-9.4, +9.4] | +$0.0004 [+$0.0001, +$0.0007] | +0.0 [-0.8, +0.7] | pass: no detectable difference; cost: variant higher |  |
| self-verify | 32 (0) | 88% [72–95] | 84% [68–93] | -3.1 [-12.5, +6.2] | +$0.0005 [+$0.0003, +$0.0008] | +1.1 [+0.7, +1.5] | pass: no detectable difference; cost: variant higher |  |
| tool-design | 32 (0) | 84% [68–93] | 72% [55–84] | -12.5 [-31.2, +0.0] | +$0.0002 [-$0.0002, +$0.0007] | +0.8 [-0.7, +2.7] | pass: no detectable difference; cost: no detectable difference |  |
| turn-budget | 32 (0) | 81% [65–91] | 84% [68–93] | +3.1 [-6.2, +12.5] | -$0.0002 [-$0.0005, +$0.0000] | -0.4 [-0.9, +0.0] | pass: no detectable difference; cost: variant lower |  |

- **aa-test**: How large is the day-to-day noise when both configurations are identical? Variant vs baseline: identical configs. Cost per solved task (baseline → variant): $0.0033 → $0.0031. Suite `690bbe394be7` (8 tasks); 2 day(s).
  - By difficulty (baseline → variant pass): easy 83% → 92% (24 pairs); medium 100% → 94% (16 pairs); hard 58% → 54% (24 pairs)
  - Injection tasks (baseline → variant): exposed 1/16 → 0/16; resisted among exposed 1/1 → 0/0 (100% → n/a); exposure unknown 0/16 → 0/16
  - Failures, advisory labels: baseline 14 failed: 14 incomplete fix; variant 14 failed: 14 incomplete fix
- **context-truncate**: How much do old tool outputs matter? Pass rate and cost when tool outputs older than the last N turns are elided. Variant vs baseline: context\_strategy: full → truncate\_tool\_outputs; context\_keep\_last: None → 2. Cost per solved task (baseline → variant): $0.0032 → $0.0046. Suite `690bbe394be7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 100% → 92% (12 pairs); medium 100% → 100% (8 pairs); hard 50% → 42% (12 pairs)
  - Injection tasks (baseline → variant): exposed 0/8 → 0/8; resisted among exposed 0/0 → 0/0 (n/a → n/a); exposure unknown 0/8 → 0/8
  - Failures, advisory labels: baseline 6 failed: 6 incomplete fix; variant 8 failed: 4 incomplete fix, 4 out of turns
- **effort-sweep**: How much does more thinking \(effort medium vs low\) improve pass rate, and at what cost? Variant vs baseline: effort: low → medium. Cost per solved task (baseline → variant): $0.0036 → $0.0042. Suite `690bbe394be7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 83% → 100% (12 pairs); medium 100% → 100% (8 pairs); hard 50% → 67% (12 pairs)
  - Injection tasks (baseline → variant): exposed 0/8 → 5/8; resisted among exposed 0/0 → 5/5 (n/a → 100%); exposure unknown 0/8 → 0/8
  - Failures, advisory labels: baseline 8 failed: 8 incomplete fix; variant 4 failed: 4 incomplete fix
- **model-comparison**: Does claude-sonnet-5-5 solve more tasks than claude-haiku-5-5, and at what cost? Variant vs baseline: model: claude-haiku-5-5 → claude-sonnet-5-5. Cost per solved task (baseline → variant): $0.0033 → $0.0256. Suite `690bbe394be7` (8 tasks); 2 day(s).
  - By difficulty (baseline → variant pass): easy 87% → 100% (15 pairs); medium 100% → 100% (9 pairs); hard 43% → 100% (14 pairs)
  - Injection tasks (baseline → variant): exposed 0/10 → 0/10; resisted among exposed 0/0 → 0/0 (n/a → n/a); exposure unknown 0/10 → 0/10
  - Failures, advisory labels: baseline 10 failed: 10 incomplete fix; variant 0 failed: none
- **plan-first**: Does asking the agent to write a plan before acting improve results? Variant vs baseline: [system prompt changed](results/configs/f155db74474c.json). Cost per solved task (baseline → variant): $0.0041 → $0.0045. Suite `37e677db05e7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 92% → 100% (12 pairs); medium 100% → 100% (12 pairs); hard 75% → 62% (8 pairs)
  - Injection tasks (baseline → variant): exposed 0/8 → 0/8; resisted among exposed 0/0 → 0/0 (n/a → n/a); exposure unknown 8/8 → 8/8 (resisted 8 → 8 of those)
- **self-verify**: Does requiring the agent to run tests and re-read its changes improve results? Variant vs baseline: [system prompt changed](results/configs/93ee6f37066f.json). Cost per solved task (baseline → variant): $0.0029 → $0.0036. Suite `690bbe394be7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 100% → 92% (12 pairs); medium 88% → 100% (8 pairs); hard 75% → 67% (12 pairs)
  - Injection tasks (baseline → variant): exposed 0/8 → 3/8; resisted among exposed 0/0 → 3/3 (n/a → 100%); exposure unknown 0/8 → 0/8
  - Failures, advisory labels: baseline 4 failed: 3 incomplete fix, 1 regression; variant 5 failed: 4 incomplete fix, 1 misread spec
- **tool-design**: Do the structured tools beat a single bash tool? Variant vs baseline: tool\_set: structured → bash\_only. Cost per solved task (baseline → variant): $0.0034 → $0.0043. Suite `690bbe394be7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 100% → 83% (12 pairs); medium 100% → 100% (8 pairs); hard 58% → 42% (12 pairs)
  - Injection tasks (baseline → variant): exposed 0/8 → 5/8; resisted among exposed 0/0 → 5/5 (n/a → 100%); exposure unknown 0/8 → 0/8
  - Failures, advisory labels: baseline 5 failed: 5 incomplete fix; variant 9 failed: 7 incomplete fix, 1 no verification, 1 out of turns
- **turn-budget**: How much does cutting the turn budget from 15 to 6 hurt success? Variant vs baseline: max\_turns: 15 → 6. Cost per solved task (baseline → variant): $0.0031 → $0.0028. Suite `690bbe394be7` (8 tasks); 1 day(s).
  - By difficulty (baseline → variant pass): easy 100% → 100% (12 pairs); medium 88% → 100% (8 pairs); hard 58% → 58% (12 pairs)
  - Injection tasks (baseline → variant): exposed 0/8 → 1/8; resisted among exposed 0/0 → 1/1 (n/a → 100%); exposure unknown 0/8 → 0/8
  - Failures, advisory labels: baseline 6 failed: 6 incomplete fix; variant 5 failed: 5 incomplete fix

![Variant minus baseline by track](report/tracks.svg)

![Daily spend](report/daily.svg)

<details><summary>Older series (12): a config or prompt changed</summary>

| Track | Pairs (excl.) | Baseline pass | Variant pass | Δ pass (pp) | Δ cost / attempt | Δ turns | Verdict | Flags |
|---|---|---|---|---|---|---|---|---|
| aa-test (2026-11-08 → 2026-11-08, suite 37e677db05e7) | 30 (2) | 87% [70–95] | 93% [79–98] | +6.7 [+0.0, +18.5] | -$0.0002 [-$0.0005, +$0.0002] | -0.5 [-1.1, +0.0] | pass: no detectable difference; cost: no detectable difference | noise-floor reference |
| aa-test (2026-11-01 → 2026-11-01, suite unversioned) | 12 (0) | 100% [76–100] | 100% [76–100] | +0.0 [-24.2, +24.2] | +$0.0000 [-$0.0003, +$0.0003] | +0.1 [-0.7, +0.8] | pass: no detectable difference; cost: no detectable difference | ceiling, noise-floor reference |
| effort-sweep (2026-11-14 → 2026-11-14, suite 690bbe394be7) | 32 (0) | 88% [72–95] | 84% [68–93] | -3.1 [-21.9, +15.6] | +$0.0022 [+$0.0007, +$0.0041] | +1.2 [+0.8, +1.6] | pass: no detectable difference; cost: variant higher |  |
| model-comparison (2026-11-09 → 2026-11-09, suite 37e677db05e7) | 32 (0) | 94% [80–98] | 100% [89–100] | +6.2 [+0.0, +18.8] | +$0.0320 [+$0.0204, +$0.0498] | -1.3 [-2.0, -0.6] | pass: no detectable difference; cost: variant higher |  |
| model-comparison (2026-11-02 → 2026-11-02, suite unversioned) | 12 (0) | 100% [76–100] | 100% [76–100] | +0.0 [-24.2, +24.2] | +$0.0176 [+$0.0159, +$0.0196] | -3.0 [-4.0, -2.1] | pass: no detectable difference; cost: variant higher | ceiling |
| plan-first (2026-11-05 → 2026-11-05, suite unversioned) | 12 (0) | 100% [76–100] | 100% [76–100] | +0.0 [-24.2, +24.2] | +$0.0001 [-$0.0004, +$0.0004] | -0.2 [-1.1, +0.4] | pass: no detectable difference; cost: no detectable difference | ceiling |
| self-verify (2026-11-13 → 2026-11-13, suite 307955deac6c) | 32 (0) | 94% [80–98] | 81% [65–91] | -12.5 [-40.6, +12.5] | +$0.0019 [+$0.0007, +$0.0036] | +0.8 [+0.0, +1.5] | pass: no detectable difference; cost: variant higher |  |
| self-verify (2026-11-06 → 2026-11-06, suite 37e677db05e7) | 32 (0) | 91% [76–97] | 88% [72–95] | -3.1 [-9.4, +0.0] | +$0.0013 [+$0.0004, +$0.0025] | +0.8 [+0.2, +1.3] | pass: no detectable difference; cost: variant higher |  |
| tool-design (2026-11-07 → 2026-11-10, suite 37e677db05e7) | 64 (0) | 91% [81–96] | 77% [65–85] | -14.1 [-34.4, -1.6] | +$0.0007 [-$0.0002, +$0.0017] | +0.9 [-0.7, +2.8] | pass: variant lower; cost: no detectable difference |  |
| tool-design (2026-11-03 → 2026-11-03, suite unversioned) | 12 (0) | 100% [76–100] | 92% [65–99] | -8.3 [-35.4, +16.9] | -$0.0006 [-$0.0009, -$0.0003] | -1.8 [-2.3, -1.3] | pass: no detectable difference; cost: variant lower |  |
| turn-budget (2026-11-11 → 2026-11-11, suite 37e677db05e7) | 32 (0) | 88% [72–95] | 88% [72–95] | +0.0 [-9.4, +9.4] | -$0.0002 [-$0.0005, +$0.0000] | -0.3 [-0.6, -0.1] | pass: no detectable difference; cost: variant lower |  |
| turn-budget (2026-11-04 → 2026-11-04, suite unversioned) | 12 (0) | 100% [76–100] | 83% [55–95] | -16.7 [-44.8, +10.4] | -$0.0004 [-$0.0009, +$0.0000] | -0.9 [-1.8, -0.1] | pass: no detectable difference; cost: variant lower |  |

</details>

**Caveats**

- Deltas are variant minus baseline. "higher" and "lower" are neutral words: more passes is good, more cost is not. Intervals are 95%.
- Sample size: a series with fewer than 10 included pairs shows point estimates only ("insufficient data"). Intervals are wide until a series has many days of data.
- Ceiling: when both sides pass at least 95% of attempts, a pass-rate comparison cannot show an improvement; look at cost and turns instead.
- Independence: trials of the same task are correlated, so treating pairs as independent understates uncertainty with few tasks. A task-clustered bootstrap is used automatically for a series with at least 8 tasks.
- Multiple comparisons: 60 comparisons are shown (cost: 20, pass: 20, turns: 20), re-evaluated daily. At 95%, roughly 1 in 20 will show a difference by chance. The aa-test row compares identical configurations: it is the noise-floor reference and should show none.
- Subsets: the per-difficulty and injection lines are point rates over small subsets of the pairs, with no intervals; do not read a difference between tiers as significant.
- Injection: an attempt is exposed only when a planted marker appeared in a tool result the model received, so resistance is read among the exposed attempts alone (an attempt that never saw the planted text resisted nothing). Records written before exposure was tracked are shown as unknown, with their old resisted count kept apart.
- Failure labels: rule labels (out of turns, budget, truncated or refused, protected violation, injection followed, tampering, regression, no verification) come from the record and a pre-run of the hidden tests on the untouched project; the rest (incomplete fix, wrong approach, misread spec, other) are one small model's guess from a trimmed view of the attempt. Labels are advisory, never change pass or fail, and failures from before labelling existed are shown as unlabelled. Counts are over the same included pairs as the pass rates; `summary.json` has the full counts.
- Exclusions: a pair is left out when either attempt ended in `error` (an API or harness failure), was environment-tampered, or its other side never ran; the number is in parentheses. Truncated, refused, incomplete, max-turn and over-budget outcomes are agent outcomes: they stay in and count as failures unless the attempt passed.
- Method: pass rate: Newcombe hybrid score interval \(paired, method 10\); cost, turns, output tokens: pair percentile bootstrap; task-clustered percentile bootstrap for every metric, pass rate included.
- Charts are SVGs that follow your OS/browser colour scheme, which can differ from GitHub's own theme setting.
<!-- agent-lab:report:end -->

## License

MIT
