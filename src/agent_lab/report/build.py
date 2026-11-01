"""Build the report: README section, charts and summary.json, deterministically from results."""

import json
from dataclasses import dataclass
from typing import Any

from agent_lab.report.analysis import ResultsAnalyzer
from agent_lab.report.charts import DailySpendChart, TracksChart
from agent_lab.report.markdown import MarkdownRenderer, ReadmeUpdater
from agent_lab.report.model import ReportData
from agent_lab.runner.storage import ResultsStore


@dataclass(frozen=True)
class ReportFiles:
    """Everything a build produces, as text. A chart is None when there is nothing to draw."""

    readme: str
    tracks_svg: str | None
    daily_svg: str | None
    summary_json: str


class ReportBuilder:
    """Pure: results and the current README in, the new file contents out (nothing written)."""

    def __init__(
        self,
        analyzer: ResultsAnalyzer,
        renderer: MarkdownRenderer,
        updater: ReadmeUpdater,
        tracks_chart: TracksChart,
        daily_chart: DailySpendChart,
    ) -> None:
        self._analyzer = analyzer
        self._renderer = renderer
        self._updater = updater
        self._tracks_chart = tracks_chart
        self._daily_chart = daily_chart

    def build(self, store: ResultsStore, readme_text: str, chart_dir: str) -> ReportFiles:
        """``chart_dir`` is the directory of the SVGs as the README links to it."""
        data = self._analyzer.analyze(store)
        plotted = [
            t.current for t in data.tracks if t.current.pairs > 0
        ]  # nothing to plot otherwise
        tracks_svg = self._tracks_chart.render(plotted) if plotted else None
        daily_svg = self._daily_chart.render(data.daily) if data.daily else None
        charts: dict[str, str] = {}
        if tracks_svg:
            charts["Variant minus baseline by track"] = f"{chart_dir}/tracks.svg"
        if daily_svg:
            charts["Daily spend"] = f"{chart_dir}/daily.svg"
        section = self._renderer.render(data, charts)
        return ReportFiles(
            readme=self._updater.replace(readme_text, section),
            tracks_svg=tracks_svg,
            daily_svg=daily_svg,
            summary_json=self.summary_json(data),
        )

    @classmethod
    def summary_json(cls, data: ReportData) -> str:
        """Sorted keys, floats rounded to 6 places (and no negative zero): stable text."""
        return (
            json.dumps(cls._normalize(data.model_dump(mode="json")), sort_keys=True, indent=2)
            + "\n"
        )

    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if isinstance(value, float):
            rounded = round(value, 6)
            return 0.0 if rounded == 0 else rounded
        if isinstance(value, dict):
            return {k: cls._normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._normalize(v) for v in value]
        return value
