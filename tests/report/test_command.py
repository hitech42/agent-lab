import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from report_support import MON, realistic, run_day
from runner_support import build_cli

from agent_lab.report.markdown import END, START

README = f"# Title\n\nhand-written text\n\n{START}\nplaceholder\n{END}\n\n## License\n\nMIT\n"


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return realistic(tmp_path_factory.mktemp("cmd"))


class Project:
    """A directory holding README.md, report/ and a results/ directory to build from."""

    def __init__(self, root: Path, results: Path, readme: str = README) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.results = root, results
        self.readme = root / "README.md"
        self.readme.write_text(readme)
        self.out = root / "report"
        self.world = build_cli(root / "cli")

    def run(self, *extra: str) -> tuple[int, str]:
        start = self.world.out.tell()
        argv = ["report", "build", "--results-dir", str(self.results)]
        argv += ["--readme", str(self.readme), "--out", str(self.out), *extra]
        code = self.world.cli.run(argv)
        return code, self.world.out.getvalue()[start:]

    def snapshot(self) -> dict[str, bytes]:
        files = [self.readme, *sorted(self.out.glob("*"))] if self.out.exists() else [self.readme]
        return {p.name: p.read_bytes() for p in files}


@pytest.fixture
def project(tmp_path: Path, results: Path) -> Project:
    return Project(tmp_path / "project", results)


# --- building -------------------------------------------------------------------------


def test_build_writes_the_readme_the_charts_and_the_summary(project: Project) -> None:
    code, output = project.run()
    assert code == 0 and output.count("wrote") == 4
    assert sorted(p.name for p in project.out.iterdir()) == [
        "daily.svg",
        "summary.json",
        "tracks.svg",
    ]
    for name in ("tracks.svg", "daily.svg"):
        ET.parse(project.out / name)  # well-formed XML
    text = project.readme.read_text()
    assert text.startswith("# Title\n\nhand-written text\n\n") and text.endswith(
        "\n\n## License\n\nMIT\n"
    )
    assert "Data through **2026-11-10**" in text and "placeholder" not in text
    assert "![Variant minus baseline by track](report/tracks.svg)" in text
    assert "![Daily spend](report/daily.svg)" in text


def test_the_chart_links_are_relative_to_the_readme(tmp_path: Path, results: Path) -> None:
    project = Project(tmp_path / "p", results)
    project.out = tmp_path / "p" / "docs" / "charts"
    project.run()
    assert "(docs/charts/tracks.svg)" in project.readme.read_text()


def test_the_summary_json_is_stable_machine_readable_text(project: Project) -> None:
    project.run()
    raw = (project.out / "summary.json").read_text()
    data = json.loads(raw)
    assert raw == json.dumps(data, sort_keys=True, indent=2) + "\n"  # sorted keys, stable layout
    assert data["latest_date"] == "2026-11-10" and data["days_run"] == 5
    assert data["comparisons_shown"] == sum(data["comparisons_by_metric"].values()) > 0
    assert {t["track"] for t in data["tracks"]} == {"aa-test", "model-comparison", "tool-design"}
    series = data["tracks"][0]["current"]
    assert {"baseline", "variant", "delta_pass_pp", "method", "excluded", "ceiling"} <= set(series)
    assert "-0.0" not in raw


def test_every_number_in_the_readme_table_is_in_the_summary(project: Project) -> None:
    project.run()
    data = json.loads((project.out / "summary.json").read_text())
    text = project.readme.read_text()
    assert f"{data['days_run']} day(s) run" in text and f"{data['attempts']} attempts" in text
    for track in data["tracks"]:
        s = track["current"]
        assert f"| {s['track']} | {s['pairs']} ({sum(s['excluded'].values())}) |" in text


def test_repeated_builds_are_byte_identical(project: Project) -> None:
    project.run()
    first = project.snapshot()
    again = project.run()
    assert again == (0, "the report is already up to date\n") and project.snapshot() == first


def test_unchanged_files_are_not_rewritten(project: Project) -> None:
    project.run()
    paths = [project.readme, *project.out.glob("*")]
    stamps = {p: p.stat().st_mtime_ns for p in paths}
    os.utime(project.readme, ns=(1, 1))
    project.run()
    assert project.readme.stat().st_mtime_ns == 1  # same content: left alone
    assert all(p.stat().st_mtime_ns == stamps[p] for p in paths if p != project.readme)


def test_two_builds_from_different_places_give_the_same_files(
    tmp_path: Path, results: Path
) -> None:
    first, second = Project(tmp_path / "a", results), Project(tmp_path / "b", results)
    first.run()
    second.run()
    assert first.snapshot() == second.snapshot()


def test_dates_come_from_the_data_not_from_the_clock(project: Project) -> None:
    project.run()
    text = project.readme.read_text() + (project.out / "summary.json").read_text()
    assert "2026-11-10" in text
    assert str(__import__("datetime").date.today().year) not in text.replace("2026", "")


# --- --check --------------------------------------------------------------------------


def test_check_passes_on_an_up_to_date_report_and_writes_nothing(project: Project) -> None:
    project.run()
    before = project.snapshot()
    assert project.run("--check") == (0, "the report is up to date\n")
    assert project.snapshot() == before


def test_check_fails_when_any_output_would_change_and_writes_nothing(project: Project) -> None:
    code, output = project.run("--check")  # nothing has been built yet
    assert (
        code == 1 and "would change" in output and "run `uv run agent-lab report build`" in output
    )
    assert not project.out.exists() and "placeholder" in project.readme.read_text()
    project.run()
    (project.out / "daily.svg").write_text("<svg/>")
    code, output = project.run("--check")
    assert code == 1 and output.count("would change") == 1 and "daily.svg" in output
    assert (project.out / "daily.svg").read_text() == "<svg/>"  # still not written


def test_check_notices_new_results(tmp_path: Path, results: Path) -> None:
    mine = Path(tmp_path / "results")
    import shutil

    shutil.copytree(results, mine)
    project = Project(tmp_path / "p", mine)
    project.run()
    assert project.run("--check")[0] == 0
    run_day(tmp_path / "later", mine, "2026-11-09")
    code, output = project.run("--check")
    assert code == 1 and "README.md" in output and "summary.json" in output


# --- no results, markers, errors ------------------------------------------------------


def test_empty_results_give_a_placeholder_and_no_charts_and_no_error(tmp_path: Path) -> None:
    project = Project(tmp_path / "p", tmp_path / "no-results")
    code, _ = project.run()
    text = project.readme.read_text()
    assert code == 0 and "No results yet" in text
    assert sorted(p.name for p in project.out.iterdir()) == ["summary.json"]
    data = json.loads((project.out / "summary.json").read_text())
    assert data["latest_date"] is None and data["tracks"] == [] and data["daily"] == []
    assert project.run("--check")[0] == 0


def test_results_without_a_complete_pair_draw_only_the_spend_chart(tmp_path: Path) -> None:
    results = tmp_path / "results"
    run_day(tmp_path / "w", results, MON, trials=1)
    path = results / "runs" / f"{MON}.jsonl"
    path.write_text("".join(path.read_text().splitlines(keepends=True)[::2]))  # one side only
    project = Project(tmp_path / "p", results)
    assert project.run()[0] == 0
    assert sorted(p.name for p in project.out.iterdir()) == ["daily.svg", "summary.json"]
    assert "| model-comparison | 0 (3) |" in project.readme.read_text()  # excluded, not hidden


def test_a_chart_that_is_no_longer_produced_is_removed(tmp_path: Path, results: Path) -> None:
    project = Project(tmp_path / "p", results)
    project.run()
    project.results = tmp_path / "empty"
    assert project.run("--check")[0] == 1
    project.run()
    assert sorted(p.name for p in project.out.iterdir()) == ["summary.json"]


@pytest.mark.parametrize(
    "readme",
    ["no markers", f"{START}\nonly start", f"{START}\n{END}\n{START}\n{END}", f"{END}\n{START}"],
)
def test_missing_or_duplicate_markers_fail_without_writing(
    tmp_path: Path, results: Path, readme: str
) -> None:
    project = Project(tmp_path / "p", results, readme)
    code, output = project.run()
    assert code == 1 and output.startswith("error:") and "README.md" in output
    assert project.readme.read_text() == readme and not project.out.exists()
    assert project.run("--check")[0] == 1


def test_a_missing_readme_is_an_error(tmp_path: Path, results: Path) -> None:
    project = Project(tmp_path / "p", results)
    project.readme.unlink()
    code, output = project.run()
    assert code == 1 and "cannot read" in output


def test_an_unwritable_output_directory_is_an_error(tmp_path: Path, results: Path) -> None:
    project = Project(tmp_path / "p", results)
    (tmp_path / "p" / "report").write_text("a file, not a directory")
    code, output = project.run()
    assert code == 1 and "cannot write the report" in output


def test_the_results_are_never_modified(project: Project, results: Path) -> None:
    before = {p: p.read_bytes() for p in results.rglob("*") if p.is_file()}
    project.run()
    project.run("--check")
    assert {p: p.read_bytes() for p in results.rglob("*") if p.is_file()} == before


def test_report_build_has_documented_defaults() -> None:
    from agent_lab.runner.cli import Cli

    args = Cli._parser().parse_args(["report", "build"])
    assert (args.results_dir, args.readme, args.out, args.check) == (
        "results",
        "README.md",
        "report",
        False,
    )
