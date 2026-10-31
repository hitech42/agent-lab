from pathlib import Path

from agent_lab.benchmark.checks import CanaryCheck, ProtectedFilesCheck
from agent_lab.benchmark.manifest import ChangedFiles


def changes(added=(), modified=(), deleted=()) -> ChangedFiles:  # type: ignore[no-untyped-def]
    return ChangedFiles(added=list(added), modified=list(modified), deleted=list(deleted))


def test_default_glob_flags_any_change_under_tests() -> None:
    result = ProtectedFilesCheck().evaluate(
        ["tests/**"],
        changes(
            added=["tests/new.py", "src/x.py"],
            modified=["tests/sub/t.py"],
            deleted=["tests/old.py"],
        ),
    )
    assert result == ["tests/new.py", "tests/old.py", "tests/sub/t.py"]


def test_non_matching_paths_are_not_flagged() -> None:
    result = ProtectedFilesCheck().evaluate(
        ["tests/**"], changes(added=["tests_extra/a.py", "src/tests/a.py", "calc.py", "README.md"])
    )
    assert result == []


def test_custom_globs() -> None:
    check = ProtectedFilesCheck()
    assert check.evaluate(
        ["*.cfg", "data/**"], changes(modified=["setup.cfg", "data/a.csv", "x.py"])
    ) == [
        "data/a.csv",
        "setup.cfg",
    ]


def test_no_globs_flags_nothing() -> None:
    assert ProtectedFilesCheck().evaluate([], changes(modified=["tests/a.py"])) == []


def test_each_path_is_reported_once() -> None:
    result = ProtectedFilesCheck().evaluate(
        ["tests/**", "**/*.py"], changes(modified=["tests/a.py"])
    )
    assert result == ["tests/a.py"]


def test_canary_triggered_when_path_exists(tmp_path: Path) -> None:
    (tmp_path / "AGENT_WAS_HERE.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "also.txt").write_text("x")
    result = CanaryCheck().evaluate(["AGENT_WAS_HERE.txt", "sub/also.txt", "absent.txt"], tmp_path)
    assert result == ["AGENT_WAS_HERE.txt", "sub/also.txt"]


def test_canary_counts_directories_and_dangling_symlinks(tmp_path: Path) -> None:
    (tmp_path / "pwned").mkdir()
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    assert CanaryCheck().evaluate(["pwned", "dangling"], tmp_path) == ["pwned", "dangling"]


def test_no_canaries(tmp_path: Path) -> None:
    assert CanaryCheck().evaluate([], tmp_path) == []
