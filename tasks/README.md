# Tasks

Each task lives in `tasks/<task_id>/` and is scored objectively by `agent_lab.benchmark`.

```
tasks/<task_id>/
  task.yaml       id, title, category, difficulty, prompt, grading, protected, canaries, expected_answer
  project/        the codebase the agent works on, including visible tests
  hidden_tests/   grader-only tests; never placed in the sandbox before the attempt ends
  solution/       reference solution: files that overwrite project/ files; never shown to the agent
```

The full format, the authoring rules and the validation requirement are in the
"Task authoring" section of `CLAUDE.md`.

Validate every task (no API access needed):

```
uv run agent-lab validate-tasks
```

`difficulty` is `easy`, `medium` or `hard`: easy and medium projects are at most 200 lines of code,
hard ones at most 600. The suite and task hashes recorded with every result cover `task.yaml`,
`project/` and `hidden_tests/`.

Run one real attempt at a task and grade it (needs `ANTHROPIC_API_KEY`; manual only):

```
uv run python scripts/run_task.py --task fix-date-range [--tool-set bash_only] [--model claude-haiku-5-5]
```
