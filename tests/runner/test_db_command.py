import sqlite3
from pathlib import Path

from runner_support import MONDAY, auth_error, build_cli, default_responder, is_warmup, status_error

from agent_lab.runner.storage import ResultsStore


def rows(db: Path, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def finished_day(tmp_path: Path):  # type: ignore[no-untyped-def]
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "2")
    return world


def rebuild(world, db: Path) -> str:  # type: ignore[no-untyped-def]
    start = world.out.tell()
    code = world.cli.run(
        ["db", "rebuild", "--db", str(db), "--results-dir", str(world.results_dir)]
    )
    assert code == 0
    return world.out.getvalue()[start:]


def test_rebuild_creates_attempts_runs_aborts_and_the_pairs_view(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    db = tmp_path / "results.db"
    output = rebuild(world, db)
    assert f"wrote {db}: 12 attempts, 1 runs, 0 aborts" in output
    assert rows(db, "SELECT count(*) FROM attempts") == [(12,)]
    assert rows(db, "SELECT count(*) FROM runs") == [(1,)]
    assert rows(db, "SELECT count(*) FROM aborts") == [(0,)]
    assert rows(db, "SELECT status, abort_count FROM runs") == [("completed", 0)]


def test_columns_have_useful_types(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    db = tmp_path / "results.db"
    rebuild(world, db)
    types = {r[1]: r[2] for r in rows(db, "PRAGMA table_info(attempts)")}
    assert types["passed"] == "INTEGER" and types["cost_usd"] == "REAL"
    assert types["task_id"] == "TEXT" and types["trial"] == "INTEGER"
    assert rows(db, "SELECT sum(passed), round(sum(cost_usd), 6) > 0 FROM attempts")[0][0] == 12


def test_pairs_view_has_one_row_per_baseline_variant_pair(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    db = tmp_path / "results.db"
    rebuild(world, db)
    assert rows(db, "SELECT count(*) FROM pairs") == [(6,)]  # 3 tasks x 2 trials
    sample = rows(
        db,
        "SELECT baseline_hash != variant_hash, baseline_passed, variant_passed, "
        "baseline_cost_usd < variant_cost_usd FROM pairs LIMIT 1",
    )
    assert sample == [(1, 1, 1, 1)]  # sonnet costs more than haiku in the fake run
    keys = rows(db, "SELECT DISTINCT run_id, task_id, trial FROM pairs")
    assert len(keys) == 6


def test_unpaired_attempts_do_not_appear_in_the_view(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    world.run("--date", MONDAY, "--trials", "1")
    store = ResultsStore(world.results_dir)
    path = store.attempts_path(MONDAY)
    path.write_text("\n".join(path.read_text().splitlines()[:5]) + "\n")  # half of the last pair
    db = tmp_path / "results.db"
    rebuild(world, db)
    assert rows(db, "SELECT count(*) FROM attempts") == [(5,)]
    assert rows(db, "SELECT count(*) FROM pairs") == [(2,)]


def test_abort_files_are_ingested_into_the_aborts_table(tmp_path: Path) -> None:
    def responder(request):  # type: ignore[no-untyped-def]
        return auth_error() if is_warmup(request) else default_responder(request)

    world = build_cli(tmp_path, responder=responder)
    world.run("--date", MONDAY, "--trials", "1", "--resume")
    world.client.responder = default_responder
    world.run("--date", MONDAY, "--trials", "1", "--resume")
    db = tmp_path / "results.db"
    output = rebuild(world, db)
    assert "1 aborts" in output
    reason, error_type, status, skipped = rows(
        db, "SELECT reason, error_type, error_status, skipped_aborted FROM aborts"
    )[0]
    assert (reason, error_type, status, skipped) == ("warmup_failed", "AuthenticationError", 401, 6)
    assert rows(db, "SELECT abort_count FROM runs") == [(1,)]


def test_skipped_attempts_are_stored_as_json_text(tmp_path: Path) -> None:
    world = build_cli(tmp_path, responder=lambda request: status_error(500))
    world.run("--date", MONDAY, "--trials", "1")
    db = tmp_path / "results.db"
    rebuild(world, db)
    (blob,) = rows(db, "SELECT skipped_attempts FROM aborts")[0]
    import json

    assert len(json.loads(blob)) == 6 and json.loads(blob)[0]["reason"] == "skipped_aborted"


def test_corrupt_jsonl_lines_are_skipped_and_reported(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    path = ResultsStore(world.results_dir).attempts_path(MONDAY)
    with path.open("a") as handle:
        handle.write('{"truncated": tru\n')
    db = tmp_path / "results.db"
    output = rebuild(world, db)
    assert "skipped 1 unreadable line(s)" in output
    assert rows(db, "SELECT count(*) FROM attempts") == [(12,)]


def test_rebuild_is_repeatable_and_replaces_the_old_database(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    db = tmp_path / "sub" / "results.db"
    rebuild(world, db)
    rebuild(world, db)
    assert rows(db, "SELECT count(*) FROM attempts") == [(12,)]
    assert [p.name for p in db.parent.iterdir()] == ["results.db"]  # no temp files left behind


def test_rebuild_never_touches_the_results(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    runs = world.results_dir / "runs"
    before = {p: p.read_bytes() for p in runs.iterdir()}
    rebuild(world, tmp_path / "results.db")
    assert {p: p.read_bytes() for p in runs.iterdir()} == before


def test_rebuild_on_empty_results(tmp_path: Path) -> None:
    world = build_cli(tmp_path)
    db = tmp_path / "results.db"
    assert "0 attempts, 0 runs, 0 aborts" in rebuild(world, db)
    assert rows(db, "SELECT count(*) FROM pairs") == [(0,)]


def test_the_default_database_name_is_gitignored() -> None:
    from runner_support import REPO

    patterns = (REPO / ".gitignore").read_text().splitlines()
    assert "*.db" in patterns and ".transcripts/" in patterns


def test_configs_are_loaded_into_their_own_table(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    db = tmp_path / "results.db"
    assert "2 configs" in rebuild(world, db)
    assert rows(db, "SELECT count(*) FROM configs") == [(2,)]
    assert rows(db, "SELECT DISTINCT effort, max_tokens_per_call FROM configs") == [
        ("medium", 16000)
    ]
    joined = rows(
        db,
        "SELECT count(*) FROM attempts a JOIN configs c ON c.config_hash = a.config_hash",
    )
    assert joined == [(12,)]  # every attempt can be explained by a registered config
    prompt = rows(db, "SELECT system_prompt FROM configs WHERE first_name = 'baseline'")[0][0]
    assert prompt.strip() and "name" not in {r[1] for r in rows(db, "PRAGMA table_info(configs)")}


def test_attempts_and_the_database_expose_effort_and_dirty_flag(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    db = tmp_path / "results.db"
    rebuild(world, db)
    assert rows(db, "SELECT DISTINCT effort FROM attempts") == [("medium",)]
    # the real repository is read here, so clean or dirty depends on the checkout: but known
    assert rows(db, "SELECT count(*) FROM attempts WHERE git_dirty IS NULL") == [(0,)]


def test_unreadable_files_of_every_kind_are_counted(tmp_path: Path) -> None:
    world = finished_day(tmp_path)
    runs = world.results_dir / "runs"
    (runs / "2026-11-03.run.json").write_text("{bad")
    (runs / "2026-11-03.abort-090000.json").write_text("{bad")
    (world.results_dir / "configs" / "broken.json").write_text("{bad")
    output = rebuild(world, tmp_path / "results.db")
    assert "skipped 3 unreadable line(s)/file(s)" in output
