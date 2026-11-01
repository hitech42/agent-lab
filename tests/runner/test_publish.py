import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from runner_support import (
    MONDAY,
    FakeGrader,
    auth_error,
    build_cli,
    build_world,
    default_responder,
    is_warmup,
    make_plan,
    with_caps,
)

from agent_lab.harness.redact import Redactor
from agent_lab.runner.publish import (
    CommitMessageBuilder,
    PublishRejected,
    ResultsPublisher,
)
from agent_lab.runner.records import AttemptRecord


def tree(root: Path) -> dict[str, bytes]:
    """Every file under root with its bytes."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def produce(tmp: Path, **kwargs: Any) -> Path:
    """Run a fake day with the real writers; returns its results directory."""
    plan = kwargs.pop("plan", make_plan(trials=1))
    world = build_world(tmp, plan=plan, **kwargs)
    world.runner.run(plan)
    return world.store.runs_dir.parent


def incoming_from(results: Path, dest: Path) -> Path:
    """What the experiment job uploads: the new files under runs/ and configs/."""
    for name in ("runs", "configs"):
        if (results / name).is_dir():
            shutil.copytree(results / name, dest / name, ignore=shutil.ignore_patterns(".gitkeep"))
    dest.mkdir(parents=True, exist_ok=True)
    return dest


@pytest.fixture
def day(tmp_path: Path) -> tuple[Path, Path]:
    """(incoming directory, empty repo results directory) for one finished day."""
    incoming = incoming_from(produce(tmp_path / "w"), tmp_path / "incoming")
    repo = tmp_path / "repo-results"
    (repo / "runs").mkdir(parents=True)
    return incoming, repo


def publish(incoming: Path, repo: Path, **limits: Any):  # type: ignore[no-untyped-def]
    publisher = ResultsPublisher(repo, **limits)
    return publisher.apply(publisher.plan(incoming))


def violations(incoming: Path, repo: Path, **limits: Any) -> list[str]:
    with pytest.raises(PublishRejected) as excinfo:
        ResultsPublisher(repo, **limits).plan(incoming)
    return excinfo.value.violations


def rejects(incoming: Path, repo: Path, fragment: str, **limits: Any) -> None:
    """The set is rejected for a reason mentioning fragment, and nothing is copied."""
    before = tree(repo)
    found = violations(incoming, repo, **limits)
    assert any(fragment in v for v in found), found
    assert tree(repo) == before


def jsonl(incoming: Path) -> Path:
    return next((incoming / "runs").glob("*.jsonl"))


# --- the happy paths ------------------------------------------------------------------


def test_a_new_day_is_copied_byte_for_byte(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    report = publish(incoming, repo)
    assert report.changed and report.appended == [] and report.skipped == []
    assert sorted(report.copied) == sorted(tree(incoming))
    assert {k: v for k, v in tree(repo).items()} == tree(incoming)
    assert report.unreadable_lines == 0


def test_a_resumed_day_appends_to_the_existing_jsonl(tmp_path: Path) -> None:
    full = produce(tmp_path / "w")
    incoming = incoming_from(full, tmp_path / "incoming")
    repo = tmp_path / "repo"
    shutil.copytree(full / "configs", repo / "configs")
    (repo / "runs").mkdir(parents=True)
    lines = jsonl(incoming).read_bytes().splitlines(keepends=True)
    existing = repo / "runs" / jsonl(incoming).name
    existing.write_bytes(b"".join(lines[:3]))  # the first segment was published earlier
    before = existing.read_bytes()
    report = publish(incoming, repo)
    assert report.appended == [f"runs/{existing.name}"]
    assert existing.read_bytes() == jsonl(incoming).read_bytes()
    assert existing.read_bytes().startswith(before)  # earlier bytes untouched
    assert f"runs/{MONDAY}.run.json" in report.copied
    assert all(rel.startswith("configs/") for rel in report.skipped)  # already published


def test_an_abort_only_day_is_accepted(tmp_path: Path) -> None:
    results = produce(
        tmp_path / "w", responder=lambda r: auth_error() if is_warmup(r) else default_responder(r)
    )
    incoming = incoming_from(results, tmp_path / "incoming")
    assert not list((incoming / "runs").glob("*.jsonl"))
    repo = tmp_path / "repo"
    report = publish(incoming, repo)
    assert any(".abort-" in rel for rel in report.copied) and report.changed


def test_configs_alone_are_accepted(tmp_path: Path) -> None:
    incoming = incoming_from(produce(tmp_path / "w"), tmp_path / "incoming")
    shutil.rmtree(incoming / "runs")
    report = publish(incoming, tmp_path / "repo")
    assert all(rel.startswith("configs/") for rel in report.copied) and report.copied


def test_publishing_the_same_set_twice_is_a_no_op(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    publish(incoming, repo)
    before = tree(repo)
    again = publish(incoming, repo)
    assert not again.changed and sorted(again.skipped) == sorted(before)
    assert tree(repo) == before


def test_an_empty_incoming_directory_has_nothing_to_publish(tmp_path: Path) -> None:
    (tmp_path / "incoming").mkdir()
    assert not publish(tmp_path / "incoming", tmp_path / "repo").changed


# --- path and file rules --------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "runs/notes.txt",
        "runs/2026-13-45.jsonl",
        "runs/2026-11-02.jsonl.bak",
        "runs/2026-11-02.abort-12.json",
        "runs/../escape.json",
        "configs/ZZZZZZZZZZZZ.json",
        "configs/abc.json",
        "configs/0123456789abcd.json",
        "stray.json",
    ],
)
def test_a_file_with_a_name_that_is_not_allowed_is_rejected(
    day: tuple[Path, Path], rel: str
) -> None:
    incoming, repo = day
    target = incoming / rel
    if ".." in rel:  # a literal name can't contain a slash; use an odd file instead
        target = incoming / "runs" / "..json"
    target.write_text("{}")
    rejects(incoming, repo, "not allowed")


@pytest.mark.parametrize("directory", ["other", "runs/sub", "configs/sub"])
def test_directories_other_than_runs_and_configs_are_rejected(
    day: tuple[Path, Path], directory: str
) -> None:
    incoming, repo = day
    (incoming / directory).mkdir()
    (incoming / directory / "x.json").write_text("{}")
    rejects(incoming, repo, directory.split("/")[-1])


def test_a_symlink_file_is_rejected(day: tuple[Path, Path], tmp_path: Path) -> None:
    incoming, repo = day
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (incoming / "runs" / f"{MONDAY}.run.json").unlink()
    (incoming / "runs" / f"{MONDAY}.run.json").symlink_to(outside)
    rejects(incoming, repo, "symbolic links")


def test_a_symlinked_directory_is_rejected(day: tuple[Path, Path], tmp_path: Path) -> None:
    incoming, repo = day
    elsewhere = tmp_path / "elsewhere"
    shutil.move(incoming / "runs", elsewhere)
    (incoming / "runs").symlink_to(elsewhere)
    rejects(incoming, repo, "real directory")


def test_a_non_regular_file_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    os.mkfifo(incoming / "runs" / "2026-11-03.jsonl")
    rejects(incoming, repo, "not a regular file")


def test_an_oversized_file_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    rejects(incoming, repo, "exceeds 100", max_file_bytes=100)


def test_too_many_files_are_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    rejects(incoming, repo, "too many files", max_files=2)


def test_the_incoming_path_must_be_a_directory(tmp_path: Path) -> None:
    assert "not a directory" in violations(tmp_path / "missing", tmp_path / "repo")[0]


# --- parsing and consistency ----------------------------------------------------------


@pytest.mark.parametrize("name", [f"{MONDAY}.run.json", f"{MONDAY}.abort-090000.json"])
def test_a_summary_or_abort_that_does_not_parse_is_rejected(
    day: tuple[Path, Path], name: str
) -> None:
    incoming, repo = day
    (incoming / "runs" / name).write_text('{"schema_version": 1}')
    rejects(incoming, repo, "does not parse")


def test_an_unparseable_config_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    config = next((incoming / "configs").glob("*.json"))
    config.write_text("{not json")
    rejects(incoming, repo, "does not parse as ConfigRecord")


def test_a_non_utf8_summary_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    (incoming / "runs" / f"{MONDAY}.run.json").write_bytes(b"\xff\xfe{}")
    rejects(incoming, repo, "not UTF-8")


def test_a_forged_config_whose_content_does_not_match_its_hash_is_rejected(
    day: tuple[Path, Path],
) -> None:
    incoming, repo = day
    config = next((incoming / "configs").glob("*.json"))
    data = json.loads(config.read_text())
    data["max_turns"] += 1  # same name and config_hash field, different content
    config.write_text(json.dumps(data))
    rejects(incoming, repo, "must all agree")


def test_a_config_whose_name_is_not_its_hash_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    config = next((incoming / "configs").glob("*.json"))
    config.rename(config.with_name("0" * 12 + ".json"))
    rejects(incoming, repo, "must all agree")


def test_a_blank_line_in_the_jsonl_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    lines = jsonl(incoming).read_text().splitlines()
    jsonl(incoming).write_text("\n".join([lines[0], "", *lines[1:]]) + "\n")
    rejects(incoming, repo, "line 2 is blank")


def test_a_record_dated_for_another_day_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    old = jsonl(incoming)
    old.rename(old.with_name("2026-11-03.jsonl"))
    rejects(incoming, repo, f"is dated {MONDAY}")


def test_a_summary_dated_for_another_day_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    path = incoming / "runs" / f"{MONDAY}.run.json"
    path.rename(path.with_name("2026-11-03.run.json"))
    rejects(incoming, repo, f"is dated {MONDAY}")


def test_a_duplicate_attempt_id_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    first = jsonl(incoming).read_text().splitlines()[0]
    with jsonl(incoming).open("a") as handle:
        handle.write(first + "\n")
    rejects(incoming, repo, "duplicate attempt_id")


def test_a_track_that_is_not_a_slug_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    lines = [json.loads(line) for line in jsonl(incoming).read_text().splitlines()]
    lines[0]["track"] = "evil\nfake: commit message"
    jsonl(incoming).write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    rejects(incoming, repo, "is not a slug")


# --- unreadable lines are counted, not rejected ---------------------------------------


def with_fragments(
    incoming: Path, where: str, count: int, raw: bytes = b'{"schema_version": 1, "ru'
) -> None:
    lines = jsonl(incoming).read_bytes().splitlines(keepends=True)
    if where == "end":
        data = b"".join(lines) + b"\n".join([raw] * count)
    else:  # between two valid records, as the store leaves it after a killed writer
        data = lines[0] + b"".join(raw + b"\n" for _ in range(count)) + b"".join(lines[1:])
    jsonl(incoming).write_bytes(data)


@pytest.mark.parametrize("where", ["end", "middle"])
def test_one_fragment_is_accepted_and_counted(day: tuple[Path, Path], where: str) -> None:
    incoming, repo = day
    with_fragments(incoming, where, 1)
    report = publish(incoming, repo)
    assert report.unreadable_lines == 1 and report.changed
    assert tree(repo)["runs/" + jsonl(incoming).name] == jsonl(incoming).read_bytes()


def test_a_fragment_cut_inside_a_multibyte_character_is_counted(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    with_fragments(incoming, "middle", 1, raw='{"detail": "café'.encode()[:-1])
    assert publish(incoming, repo).unreadable_lines == 1


def test_five_fragments_are_tolerated_and_six_are_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    six = incoming.parent / "six"
    shutil.copytree(incoming, six)
    with_fragments(incoming, "middle", 5)
    assert publish(incoming, repo).unreadable_lines == 5
    with_fragments(six, "middle", 6)
    rejects(six, repo.parent / "other-repo", "6 unreadable lines")


def test_fragments_do_not_excuse_other_violations(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    with_fragments(incoming, "middle", 1)
    (incoming / "runs" / f"{MONDAY}.run.json").write_text("{}")
    rejects(incoming, repo, "does not parse")


# --- against what is already published ------------------------------------------------


def test_a_modified_existing_summary_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    publish(incoming, repo)
    path = incoming / "runs" / f"{MONDAY}.run.json"
    data = json.loads(path.read_text())
    data["total_cost_usd"] = 0.0
    path.write_text(json.dumps(data))
    rejects(incoming, repo, "differs from the existing file")


def test_a_modified_existing_config_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    publish(incoming, repo)
    config = next((incoming / "configs").glob("*.json"))
    config.write_text(config.read_text().replace('"first_name"', '"first_name" ', 1))
    rejects(incoming, repo, "differs from the existing file")


def test_a_jsonl_that_rewrites_history_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    publish(incoming, repo)
    lines = jsonl(incoming).read_text().splitlines()
    first = json.loads(lines[0])
    first["passed"] = not first["passed"]
    jsonl(incoming).write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n")
    rejects(incoming, repo, "rewrite history")


def test_a_jsonl_shorter_than_the_existing_one_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    publish(incoming, repo)
    lines = jsonl(incoming).read_bytes().splitlines(keepends=True)
    jsonl(incoming).write_bytes(b"".join(lines[:-1]))
    rejects(incoming, repo, "rewrite history")


def test_an_existing_path_that_is_not_a_regular_file_is_rejected(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    (repo / "runs" / f"{MONDAY}.run.json").mkdir()
    rejects(incoming, repo, "not a regular file")


def test_one_violation_means_nothing_at_all_is_copied(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    (incoming / "runs" / "stray.txt").write_text("x")  # next to a perfectly valid day
    before = tree(repo)
    with pytest.raises(PublishRejected):
        publish(incoming, repo)
    assert tree(repo) == before == {}


def test_every_violation_is_reported_not_just_the_first(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    (incoming / "stray.txt").write_text("x")
    (incoming / "runs" / "bad name.txt").write_text("x")
    (incoming / "runs" / f"{MONDAY}.run.json").write_text("{}")
    assert len(violations(incoming, repo)) >= 3


# --- commit messages ------------------------------------------------------------------


def message(incoming: Path, redactor: Redactor | None = None) -> str:
    return CommitMessageBuilder(redactor).build(incoming)


def test_message_for_a_completed_day(day: tuple[Path, Path]) -> None:
    incoming, _ = day
    assert re.fullmatch(
        r"eval\(model-comparison\): baseline 3/3 vs variant 3/3 passed, \$\d+\.\d{4} \[completed\]",
        message(incoming),
    )


def test_message_counts_passes_per_role(tmp_path: Path) -> None:
    results = produce(tmp_path / "w", grader=FakeGrader(passed=False))
    assert "baseline 0/3 vs variant 0/3 passed" in message(incoming_from(results, tmp_path / "i"))


def test_message_for_a_partial_budget_day(tmp_path: Path) -> None:
    plan = with_caps(make_plan(trials=1), daily=0.07)
    incoming = incoming_from(produce(tmp_path / "w", plan=plan), tmp_path / "i")
    text = message(incoming)
    assert text.endswith("[partial_budget]") and "baseline 1/1 vs variant 1/1" in text


def test_message_for_a_tampered_day(tmp_path: Path) -> None:
    results = produce(tmp_path / "w", grader=FakeGrader(tamper_from_call=2))
    assert message(incoming_from(results, tmp_path / "i")).endswith("[aborted_tampered]")


def test_message_for_an_abort_uses_the_http_status(tmp_path: Path) -> None:
    results = produce(
        tmp_path / "w", responder=lambda r: auth_error() if is_warmup(r) else default_responder(r)
    )
    assert (
        message(incoming_from(results, tmp_path / "i"))
        == "eval(model-comparison): aborted (warmup_failed: 401), day resumable"
    )


def test_message_for_an_abort_without_a_status_uses_the_error_type(tmp_path: Path) -> None:
    results = produce(tmp_path / "w", responder=lambda r: ValueError("boom"))
    text = message(incoming_from(results, tmp_path / "i"))
    assert text == "eval(model-comparison): aborted (warmup_failed: ValueError), day resumable"


def test_message_for_a_run_that_was_killed(tmp_path: Path) -> None:
    results = produce(tmp_path / "w")
    incoming = incoming_from(results, tmp_path / "i")
    (incoming / "runs" / f"{MONDAY}.run.json").unlink()
    assert message(incoming) == "eval(model-comparison): partial results, 6 attempts, day resumable"


def test_message_for_configs_only(tmp_path: Path) -> None:
    incoming = incoming_from(produce(tmp_path / "w"), tmp_path / "i")
    shutil.rmtree(incoming / "runs")
    assert message(incoming) == "eval: register configs"


def test_the_latest_day_decides_the_message(tmp_path: Path) -> None:
    incoming = incoming_from(produce(tmp_path / "w"), tmp_path / "i")
    older = {
        p: p.with_name(p.name.replace("2026-11-02", "2026-11-01"))
        for p in (incoming / "runs").iterdir()
    }
    for source, target in older.items():
        source.rename(target)
    produce_again = incoming_from(
        produce(tmp_path / "w2", plan=make_plan("2026-11-03", trials=1)), tmp_path / "j"
    )
    for path in (produce_again / "runs").iterdir():
        shutil.copy(path, incoming / "runs" / path.name)
    assert "tool-design" in message(incoming)


def test_fragments_are_mentioned_in_the_message(day: tuple[Path, Path]) -> None:
    incoming, _ = day
    with_fragments(incoming, "middle", 1)
    assert message(incoming).endswith("[completed], 1 unreadable line")
    with_fragments(incoming, "middle", 2)
    assert message(incoming).endswith(", 3 unreadable lines")  # the earlier one is still there


def test_the_message_is_one_redacted_bounded_line(day: tuple[Path, Path]) -> None:
    incoming, _ = day
    text = message(incoming, Redactor(["model-comparison"]))
    assert "model-comparison" not in text and "[REDACTED]" in text
    assert "\n" not in text and len(text) <= 200
    assert message(incoming, Redactor(["completed"])).endswith("[[REDACTED]]")


def test_a_hostile_track_never_reaches_the_subject(day: tuple[Path, Path]) -> None:
    incoming, _ = day
    summary = incoming / "runs" / f"{MONDAY}.run.json"
    data = json.loads(summary.read_text())
    data["track"] = "x\n::set-output name=pwn::1"
    summary.write_text(json.dumps(data))
    text = message(incoming)
    assert "\n" not in text and "::set-output" not in text and text.startswith("eval(unknown)")


# --- the command line -----------------------------------------------------------------


def run_cli(tmp_path: Path, *args: str) -> tuple[int, str]:
    world = build_cli(tmp_path / "cli")
    code = world.cli.run(list(args))
    return code, world.out.getvalue()


def test_publish_and_commit_message_through_the_cli(day: tuple[Path, Path], tmp_path: Path) -> None:
    incoming, repo = day
    code, output = run_cli(
        tmp_path, "results", "publish", str(incoming), "--results-dir", str(repo)
    )
    assert code == 0 and "published" in output and "new" in output
    assert tree(repo) == tree(incoming)
    code, output = run_cli(tmp_path, "results", "commit-message", str(incoming))
    assert code == 0 and output.startswith("eval(model-comparison):") and output.count("\n") == 1


def test_publishing_nothing_new_exits_zero(day: tuple[Path, Path], tmp_path: Path) -> None:
    incoming, repo = day
    publish(incoming, repo)
    code, output = run_cli(
        tmp_path, "results", "publish", str(incoming), "--results-dir", str(repo)
    )
    assert code == 0 and "nothing new to publish" in output


def test_a_rejection_exits_nonzero_lists_the_problems_and_copies_nothing(
    day: tuple[Path, Path], tmp_path: Path
) -> None:
    incoming, repo = day
    (incoming / "stray.txt").write_text("x")
    code, output = run_cli(
        tmp_path, "results", "publish", str(incoming), "--results-dir", str(repo)
    )
    assert code == 1 and "nothing was copied" in output and "stray.txt" in output
    assert tree(repo) == {}


def test_the_cli_warns_about_unreadable_lines(day: tuple[Path, Path], tmp_path: Path) -> None:
    incoming, repo = day
    with_fragments(incoming, "end", 1)
    code, output = run_cli(
        tmp_path, "results", "publish", str(incoming), "--results-dir", str(repo)
    )
    assert code == 0 and "warning: 1 unreadable line(s)" in output


def test_a_results_subcommand_is_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run_cli(tmp_path, "results")


def test_records_written_by_the_store_still_pass_through_unchanged(day: tuple[Path, Path]) -> None:
    incoming, repo = day
    publish(incoming, repo)
    for line in (repo / "runs" / jsonl(incoming).name).read_text().splitlines():
        assert AttemptRecord.model_validate_json(line).date == MONDAY
