"""`agent-lab report build`: write the report files, or with --check say what would change."""

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TextIO

from agent_lab.report.build import ReportBuilder
from agent_lab.report.markdown import ReadmeMarkersError
from agent_lab.runner.storage import ResultsStore


class ReportCommand:
    def __init__(self, builder: ReportBuilder, stdout: TextIO) -> None:
        self._builder = builder
        self._out = stdout

    def build(self, results_dir: Path, readme: Path, out_dir: Path, check: bool) -> int:
        try:
            current = readme.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return self._error(f"cannot read {readme}: {exc}")
        chart_dir = Path(os.path.relpath(out_dir, readme.resolve().parent)).as_posix()
        try:
            files = self._builder.build(ResultsStore(results_dir), current, chart_dir)
        except ReadmeMarkersError as exc:
            return self._error(str(exc))
        wanted: dict[Path, str | None] = {
            readme: files.readme,
            out_dir / "tracks.svg": files.tracks_svg,
            out_dir / "daily.svg": files.daily_svg,
            out_dir / "summary.json": files.summary_json,
        }
        changes = [path for path, text in wanted.items() if self._differs(path, text)]
        if check:
            for path in changes:
                print(f"would change: {path}", file=self._out)
            if changes:
                print(
                    "the report is out of date: run `uv run agent-lab report build`", file=self._out
                )
                return 1
            print("the report is up to date", file=self._out)
            return 0
        try:
            for path in changes:
                self._write(path, wanted[path])
        except OSError as exc:
            return self._error(f"cannot write the report: {exc}")
        for path in changes:
            print(f"{'removed' if wanted[path] is None else 'wrote'} {path}", file=self._out)
        if not changes:
            print("the report is already up to date", file=self._out)
        return 0

    @staticmethod
    def _differs(path: Path, text: str | None) -> bool:
        if text is None:
            return path.exists()  # a chart that is no longer produced is stale
        try:
            return path.read_text(encoding="utf-8") != text
        except OSError, UnicodeDecodeError:
            return True

    @staticmethod
    def _write(path: Path, text: str | None) -> None:
        if text is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.replace(temp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)

    def _error(self, message: str) -> int:
        print(f"error: {message}", file=self._out)
        return 1
