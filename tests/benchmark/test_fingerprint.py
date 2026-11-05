"""Suite and task hashes: stable for identical content, sensitive to anything an agent sees."""

import re
import shutil
from pathlib import Path

import pytest
from task_builders import write_task

from agent_lab.benchmark.loader import TaskSuite


def make_suite(root: Path, *ids: str) -> Path:
    for task_id in ids or ("alpha", "beta"):
        write_task(root, task_id)
    return root


def fingerprint(root: Path, ids: list[str] | None = None):  # type: ignore[no-untyped-def]
    return TaskSuite.load(root).fingerprint(ids)


def test_hashes_are_twelve_hex_characters(tmp_path: Path) -> None:
    result = fingerprint(make_suite(tmp_path))
    assert re.fullmatch(r"[0-9a-f]{12}", result.suite_hash)
    assert set(result.task_hashes) == {"alpha", "beta"}
    assert all(re.fullmatch(r"[0-9a-f]{12}", h) for h in result.task_hashes.values())
    assert result.task_hashes["alpha"] != result.task_hashes["beta"]


def test_the_same_content_gives_the_same_hash_wherever_it_lives(tmp_path: Path) -> None:
    first = make_suite(tmp_path / "one")
    second = tmp_path / "two" / "elsewhere"
    shutil.copytree(first, second)
    assert fingerprint(first) == fingerprint(second)


def test_modification_times_and_caches_do_not_matter(tmp_path: Path) -> None:
    root = make_suite(tmp_path)
    before = fingerprint(root)
    (root / "alpha" / "project" / "calc.py").touch()
    (root / "alpha" / "project" / "__pycache__").mkdir()
    (root / "alpha" / "project" / "__pycache__" / "calc.pyc").write_bytes(b"junk")
    (root / "alpha" / "project" / "stray.pyc").write_bytes(b"junk")
    (root / "alpha" / "project" / ".DS_Store").write_bytes(b"finder")
    (root / "alpha" / ".DS_Store").write_bytes(b"finder")
    assert fingerprint(root) == before


@pytest.mark.parametrize(
    "path",
    [
        "task.yaml",
        "project/calc.py",
        "project/tests/test_calc.py",
        "hidden_tests/test_hidden.py",
    ],
)
def test_editing_anything_the_agent_or_the_grading_depends_on_changes_the_hashes(
    tmp_path: Path, path: str
) -> None:
    root = make_suite(tmp_path)
    before = fingerprint(root)
    target = root / "alpha" / path
    target.write_text(target.read_text() + "\n# edited\n")
    after = fingerprint(root)
    assert after.suite_hash != before.suite_hash
    assert after.task_hashes["alpha"] != before.task_hashes["alpha"]
    assert after.task_hashes["beta"] == before.task_hashes["beta"]


def test_adding_a_file_or_renaming_one_changes_the_hash(tmp_path: Path) -> None:
    root = make_suite(tmp_path)
    before = fingerprint(root).task_hashes["alpha"]
    (root / "alpha" / "project" / "extra.py").write_text("X = 1\n")
    added = fingerprint(root).task_hashes["alpha"]
    assert added != before
    (root / "alpha" / "project" / "extra.py").rename(root / "alpha" / "project" / "other.py")
    assert fingerprint(root).task_hashes["alpha"] != added


def test_the_solution_is_not_part_of_the_hash(tmp_path: Path) -> None:
    root = make_suite(tmp_path)
    before = fingerprint(root)
    solution = root / "alpha" / "solution" / "calc.py"
    solution.write_text(solution.read_text() + "# a different but still valid fix\n")
    assert fingerprint(root) == before


def test_adding_or_removing_a_task_changes_the_suite_hash_only(tmp_path: Path) -> None:
    root = make_suite(tmp_path)
    before = fingerprint(root)
    write_task(root, "gamma")
    added = fingerprint(root)
    assert added.suite_hash != before.suite_hash
    assert added.task_hashes["alpha"] == before.task_hashes["alpha"]
    shutil.rmtree(root / "gamma")
    assert fingerprint(root) == before


def test_a_subset_has_its_own_suite_hash_but_the_same_task_hashes(tmp_path: Path) -> None:
    root = make_suite(tmp_path)
    full = fingerprint(root)
    subset = fingerprint(root, ["alpha"])
    assert subset.suite_hash != full.suite_hash
    assert subset.task_hashes == {"alpha": full.task_hashes["alpha"]}
    assert fingerprint(root, ["alpha", "alpha"]) == subset  # order and repeats do not matter
    assert fingerprint(root, ["beta", "alpha"]).suite_hash == full.suite_hash


def test_an_unknown_task_id_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        fingerprint(make_suite(tmp_path), ["nope"])
