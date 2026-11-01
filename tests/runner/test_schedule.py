from datetime import date
from pathlib import Path

import pytest
from runner_support import SCHEDULE_PATH, prices, schedule, write_schedule

from agent_lab.harness.config import AgentConfig
from agent_lab.runner.schedule import ExperimentSchedule, ScheduleError

EXPECTED = {
    "2026-11-02": "model-comparison",
    "2026-11-03": "tool-design",
    "2026-11-04": "turn-budget",
    "2026-11-05": "plan-first",
    "2026-11-06": "self-verify",
    "2026-11-07": "tool-design",
    "2026-11-08": "aa-test",
}


def load(path: Path) -> ExperimentSchedule:
    return ExperimentSchedule.from_yaml(path, prices())


def problems(tmp_path: Path, mutate) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ScheduleError) as excinfo:
        load(write_schedule(tmp_path, mutate))
    return str(excinfo.value)


def test_every_weekday_resolves_to_its_track() -> None:
    sched = schedule()
    for day, name in EXPECTED.items():
        assert sched.track_for(date.fromisoformat(day)).track.name == name


def test_baseline_and_defaults() -> None:
    sched = schedule()
    base = sched.baseline
    assert (base.name, base.model, base.tool_set) == ("baseline", "claude-haiku-5-5", "structured")
    assert (base.max_turns, base.context_strategy) == (15, "full")
    assert base.system_prompt.startswith("You are a coding agent")  # the prompt FILE's text
    tuesday = sched.track_for(date(2026, 11, 3))
    assert (tuesday.trials_per_task, tuesday.daily_max_cost_usd) == (4, 1.00)
    assert tuesday.attempt_max_cost_usd == 0.15
    assert sched.cost_defaults == {"claude-haiku-5-5": 0.005, "claude-sonnet-5-5": 0.06}


def test_monday_overrides_the_default_caps() -> None:
    monday = schedule().track_for(date(2026, 11, 2))
    assert monday.variant.model == "claude-sonnet-5-5"
    assert (monday.daily_max_cost_usd, monday.attempt_max_cost_usd) == (3.00, 0.50)
    assert monday.baseline.model == "claude-haiku-5-5"


def test_variants_override_only_what_they_say() -> None:
    sched = schedule()
    base = sched.baseline
    tool = sched.track_for(date(2026, 11, 3)).variant
    assert tool.tool_set == "bash_only" and tool.max_turns == base.max_turns
    turns = sched.track_for(date(2026, 11, 4)).variant
    assert turns.max_turns == 6 and turns.tool_set == base.tool_set
    plan_first = sched.track_for(date(2026, 11, 5)).variant
    assert plan_first.system_prompt.startswith(base.system_prompt)
    assert "write a short plan" in plan_first.system_prompt
    verify = sched.track_for(date(2026, 11, 6)).variant
    assert "re-read every change" in verify.system_prompt


def test_variant_is_named_after_its_track() -> None:
    assert schedule().track_for(date(2026, 11, 3)).variant.name == "tool-design"


def test_track_by_name_and_unknown_name() -> None:
    sched = schedule()
    assert sched.track_named("tool-design").weekday == 2
    with pytest.raises(ScheduleError, match="unknown track 'nope'"):
        sched.track_named("nope")


def test_aa_test_variant_is_identical_to_the_baseline() -> None:
    sunday = schedule().track_for(date(2026, 11, 8))
    hash_of = ExperimentSchedule.config_hash
    assert hash_of(sunday.baseline) == hash_of(sunday.variant)
    assert sunday.baseline.name != sunday.variant.name  # renaming never changes the hash


# --- validation ------------------------------------------------------------------------


def test_override_that_is_not_an_agentconfig_field(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["tracks"][3]["variant"].update({"temperature": 0.2}))
    assert "track 3 (turn-budget)" in text and "temperature" in text


def test_unregistered_model(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["tracks"][1]["variant"].update({"model": "gpt-9"}))
    assert "gpt-9" in text and "pricing.yaml" in text


def test_invalid_literal_value(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["tracks"][2]["variant"].update({"tool_set": "magic"}))
    assert "tool_set" in text


def test_unimplemented_context_strategy(tmp_path: Path) -> None:
    text = problems(
        tmp_path, lambda raw: raw["tracks"][2]["variant"].update({"context_strategy": "summarize"})
    )
    assert "summarize" in text and "not implemented" in text


def test_missing_prompt_file(tmp_path: Path) -> None:
    text = problems(
        tmp_path, lambda raw: raw["tracks"][4]["variant"].update({"system_prompt": "ghost.md"})
    )
    assert "ghost.md" in text and "does not exist" in text


def test_name_cannot_be_overridden(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["tracks"][3]["variant"].update({"name": "x"}))
    assert "'name' cannot be overridden" in text


def test_missing_weekday(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["tracks"].pop(7))
    assert "exactly one entry per ISO weekday 1-7" in text


def test_weekday_outside_range(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["tracks"].update({8: raw["tracks"][1]}))
    assert "exactly one entry per ISO weekday" in text


def test_duplicate_weekday_in_the_yaml(tmp_path: Path) -> None:
    path = write_schedule(tmp_path, lambda raw: None)
    text = path.read_text() + "\n  3:\n    name: again\n    question: dup\n    variant: {}\n"
    path.write_text(text)
    with pytest.raises(ScheduleError, match="duplicate key 3"):
        load(path)


def test_same_name_with_a_different_definition(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["tracks"][6]["variant"].update({"max_turns": 9}))
    assert "name 'tool-design' is reused with a different definition" in text


def test_cost_default_required_for_every_model_used(tmp_path: Path) -> None:
    text = problems(tmp_path, lambda raw: raw["cost_defaults"].pop("claude-sonnet-5-5"))
    assert "cost_defaults has no entry for model 'claude-sonnet-5-5'" in text


def test_all_problems_are_reported_together(tmp_path: Path) -> None:
    def mutate(raw) -> None:  # type: ignore[no-untyped-def]
        raw["tracks"][1]["variant"]["model"] = "gpt-9"
        raw["tracks"][2]["variant"]["tool_set"] = "magic"

    text = problems(tmp_path, mutate)
    assert "gpt-9" in text and "tool_set" in text


def test_unknown_top_level_key_and_bad_caps(tmp_path: Path) -> None:
    assert "extra" in problems(tmp_path / "a", lambda raw: raw.update({"surprise": 1})).lower()
    assert "daily_max_cost_usd" in problems(
        tmp_path / "b", lambda raw: raw["defaults"].update({"daily_max_cost_usd": 0})
    )


def test_missing_file_is_a_schedule_error(tmp_path: Path) -> None:
    with pytest.raises(ScheduleError):
        load(tmp_path / "nope.yaml")


# --- config_hash -----------------------------------------------------------------------


def config(**changes: object) -> AgentConfig:
    base = schedule().baseline
    return base.model_copy(update=changes)


def test_hash_is_twelve_hex_and_stable() -> None:
    digest = ExperimentSchedule.config_hash(config())
    assert len(digest) == 12 and int(digest, 16) >= 0
    assert digest == ExperimentSchedule.config_hash(config())
    assert digest == ExperimentSchedule.config_hash(schedule().baseline)


def test_hash_is_pinned_so_accidental_changes_to_the_scheme_are_noticed() -> None:
    fixed = AgentConfig(
        name="anything",
        model="m",
        system_prompt="p",
        tool_set="structured",
        max_turns=1,
        max_tokens_per_call=2,
        tool_output_max_chars=3,
    )
    # Changing this value means every historical config_hash stops matching new runs.
    assert ExperimentSchedule.config_hash(fixed) == "0c55d68aa5d8"
    again = fixed.model_copy(update={"name": "other"})
    assert ExperimentSchedule.config_hash(fixed) == "0c55d68aa5d8"
    assert ExperimentSchedule.config_hash(again) == "0c55d68aa5d8"


@pytest.mark.parametrize(
    "change",
    [
        {"model": "claude-sonnet-5-5"},
        {"tool_set": "bash_only"},
        {"max_turns": 14},
        {"max_tokens_per_call": 3999},
        {"tool_output_max_chars": 3999},
        {"system_prompt": "a different prompt"},
        {"context_strategy": "summarize"},
    ],
)
def test_hash_changes_with_every_config_field(change: dict[str, object]) -> None:
    assert ExperimentSchedule.config_hash(config(**change)) != ExperimentSchedule.config_hash(
        config()
    )


def test_hash_ignores_the_name_but_not_a_one_character_prompt_edit() -> None:
    assert ExperimentSchedule.config_hash(config(name="renamed")) == ExperimentSchedule.config_hash(
        config()
    )
    edited = config(system_prompt=schedule().baseline.system_prompt + ".")
    assert ExperimentSchedule.config_hash(edited) != ExperimentSchedule.config_hash(config())


def test_real_schedule_path_exists() -> None:
    assert SCHEDULE_PATH.is_file()
