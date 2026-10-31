# agent-lab

A hand-written LLM agent harness plus a daily automated experiment. Each day a
scheduled GitHub Actions workflow runs the harness (built directly on the
Anthropic Messages API, no agent frameworks) against a fixed suite of small
Python coding tasks, compares a baseline agent config with one variant, grades
attempts with hidden pytest tests, and commits the results and an updated report.

**Note:** this repo is updated daily by an automated experiment run. Results are
committed by a scheduled workflow, not by hand.

**Status:** under construction.

## License

MIT
