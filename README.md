# agent-lab

An LLM agent harness plus a daily automated experiment. Each day a
scheduled GitHub Actions workflow runs the harness (built directly on the
Anthropic Messages API, no agent frameworks) against a fixed suite of small
Python coding tasks, compares a baseline agent config with one variant, grades
attempts with hidden pytest tests, and commits the results and an updated report.

**Status:** under construction.

## License

MIT
