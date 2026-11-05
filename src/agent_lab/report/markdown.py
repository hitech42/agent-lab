"""The README dashboard section and the marker replacement that puts it into README.md."""

import re

from agent_lab.report.model import ReportData, SeriesReport, TrackReport
from agent_lab.report.stats import INSUFFICIENT

START = "<!-- agent-lab:report:start -->"
END = "<!-- agent-lab:report:end -->"


class ReadmeMarkersError(Exception):
    """The README does not have exactly one start marker followed by one end marker."""


def escape(text: str, limit: int = 200) -> str:
    """Make free text safe to put in markdown: no markup, links, HTML or line breaks."""
    flat = " ".join(text.split())[:limit]
    flat = flat.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+!|~\"'])", r"\\\1", flat)


class ReadmeUpdater:
    """Replaces only what is between the markers; everything else is left untouched."""

    def replace(self, readme: str, section: str) -> str:
        if readme.count(START) != 1 or readme.count(END) != 1:
            raise ReadmeMarkersError(
                f"README.md must contain exactly one {START} and one {END} "
                f"(found {readme.count(START)} and {readme.count(END)})"
            )
        start, end = readme.index(START), readme.index(END)
        if end < start:
            raise ReadmeMarkersError("the end marker comes before the start marker in README.md")
        return f"{readme[: start + len(START)]}\n{section.rstrip()}\n{readme[end:]}"


class MarkdownRenderer:
    """Renders ``ReportData`` as the compact GitHub-flavoured section."""

    def __init__(self, config_dir: str = "results/configs") -> None:
        self._config_dir = config_dir

    def render(self, data: ReportData, charts: dict[str, str]) -> str:
        if data.latest_date is None:
            return "## Results\n\nNo results yet: this section fills in after the first daily run."
        lines = [
            "## Results",
            "",
            f"Data through **{data.latest_date}** · {data.days_run} day(s) run · "
            f"{data.attempts} attempts · **${data.spend_usd:.4f}** total spend "
            f"(attempts ${data.spend_attempts_usd:.4f} + warm-ups ${data.spend_warmups_usd:.4f}).",
        ]
        if data.latest_day:
            lines += ["", f"Latest day: {escape(data.latest_day)}"]
        lines += [""]
        if data.tracks:
            lines += self._table([t.current for t in data.tracks], dated=False)
            lines += ["", *self._notes(data.tracks)]
        else:
            lines += ["No complete baseline/variant pairs yet."]
        for alt, path in charts.items():
            lines += ["", f"![{alt}]({path})"]
        older = [s for t in data.tracks for s in t.older]
        if older:
            lines += [
                "",
                f"<details><summary>Older series ({len(older)}): a config or prompt "
                "changed</summary>",
                "",
                *self._table(older, dated=True),
                "",
                "</details>",
            ]
        lines += ["", *self._caveats(data)]
        return "\n".join(lines)

    # --- table and notes -------------------------------------------------------------------

    def _table(self, series: list[SeriesReport], dated: bool) -> list[str]:
        rows = [
            "| Track | Pairs (excl.) | Baseline pass | Variant pass | Δ pass (pp) | "
            "Δ cost / attempt | Δ turns | Verdict | Flags |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for s in series:
            label = escape(s.track) + (
                f" ({s.first_date} → {s.last_date}, suite {s.suite_hash or 'unversioned'})"
                if dated
                else ""
            )
            rows.append(
                f"| {label} | {s.pairs} ({s.excluded.total}) | {self._rate(s.baseline)} | "
                f"{self._rate(s.variant)} | {self._delta(s.delta_pass_pp, 1)} | "
                f"{self._cost(s)} | {self._delta(s.delta_turns, 1)} | {self._verdict(s)} | "
                f"{self._flags(s)} |"
            )
        return rows

    def _notes(self, tracks: list[TrackReport]) -> list[str]:
        notes = []
        for t in tracks:
            s = t.current
            description = escape(s.description)
            if "system prompt changed" in s.description:
                description = description.replace(
                    "system prompt changed",
                    f"[system prompt changed]({self._config_dir}/{s.variant_hash}.json)",
                )
            solved = (
                f"{self._money(s.baseline.cost_per_solved_usd)} → "
                f"{self._money(s.variant.cost_per_solved_usd)}"
            )
            question = f"{escape(s.question)} " if s.question else ""
            notes.append(
                f"- **{escape(s.track)}**: {question}Variant vs baseline: {description}. "
                f"Cost per solved task (baseline → variant): {solved}. "
                f"Suite {self._suite(s)}; {s.days} day(s)"
                + (
                    f", {t.unattributed_exclusions} unattributed exclusion(s)"
                    if t.unattributed_exclusions
                    else ""
                )
                + "."
            )
            if s.by_difficulty:
                tiers = "; ".join(
                    f"{d.difficulty} {d.baseline_rate * 100:.0f}% \u2192 "
                    f"{d.variant_rate * 100:.0f}% ({d.pairs} pairs)"
                    for d in s.by_difficulty
                )
                notes.append(f"  - By difficulty (baseline \u2192 variant pass): {tiers}")
            if s.injection is not None:
                i = s.injection
                notes.append(
                    f"  - Injection tasks resisted (baseline \u2192 variant): "
                    f"{i.baseline_resisted}/{i.pairs} \u2192 {i.variant_resisted}/{i.pairs} "
                    f"({i.baseline_rate * 100:.0f}% \u2192 {i.variant_rate * 100:.0f}%)"
                )
        return notes

    @staticmethod
    def _suite(s: SeriesReport) -> str:
        label = f"`{s.suite_hash}`" if s.suite_hash else "unversioned"
        return f"{label} ({s.suite_tasks} tasks)"

    # --- cells -------------------------------------------------------------------------------

    @staticmethod
    def _rate(side) -> str:  # type: ignore[no-untyped-def]
        text = f"{side.rate * 100:.0f}%"
        if side.ci_low is None or side.ci_high is None:
            return f"{text} ({INSUFFICIENT})"
        return f"{text} [{side.ci_low * 100:.0f}\u2013{side.ci_high * 100:.0f}]"

    @staticmethod
    def _signed(value: float, digits: int) -> str:
        rounded = round(value, digits)
        return f"{(0.0 if rounded == 0 else rounded):+.{digits}f}"

    def _delta(self, delta, digits: int) -> str:  # type: ignore[no-untyped-def]
        text = self._signed(delta.mean, digits)
        if delta.low is None or delta.high is None:
            return f"{text} ({INSUFFICIENT})"
        return f"{text} [{self._signed(delta.low, digits)}, {self._signed(delta.high, digits)}]"

    def _cost(self, s: SeriesReport) -> str:
        d = s.delta_cost_usd
        text = self._usd(d.mean)
        if d.low is None or d.high is None:
            return f"{text} ({INSUFFICIENT})"
        return f"{text} [{self._usd(d.low)}, {self._usd(d.high)}]"

    def _usd(self, value: float) -> str:
        rounded = round(value, 4)
        sign = "+" if rounded >= 0 else "-"
        return f"{sign}${abs(rounded):.4f}"

    @staticmethod
    def _money(value: float | None) -> str:
        return "n/a" if value is None else f"${value:.4f}"

    @staticmethod
    def _verdict(s: SeriesReport) -> str:
        pass_v, cost_v = s.delta_pass_pp.verdict, s.delta_cost_usd.verdict
        if pass_v == cost_v == INSUFFICIENT:
            return INSUFFICIENT
        return f"pass: {pass_v}; cost: {cost_v}"

    @staticmethod
    def _flags(s: SeriesReport) -> str:
        flags = []
        if s.ceiling:
            flags.append("ceiling")
        if s.noise_floor:
            flags.append("noise-floor reference")
        return ", ".join(flags)

    # --- caveats ---------------------------------------------------------------------------

    def _caveats(self, data: ReportData) -> list[str]:
        methods = sorted({s.method for t in data.tracks for s in [t.current, *t.older]})
        by_metric = ", ".join(f"{k}: {v}" for k, v in data.comparisons_by_metric.items())
        lines = [
            "**Caveats**",
            "",
            '- Deltas are variant minus baseline. "higher" and "lower" are neutral words: more '
            "passes is good, more cost is not. Intervals are 95%.",
            f"- Sample size: a series with fewer than {data.min_pairs} included pairs shows point "
            f'estimates only ("{INSUFFICIENT}"). Intervals are wide until a series has many '
            "days of data.",
            "- Ceiling: when both sides pass at least 95% of attempts, a pass-rate comparison "
            "cannot show an improvement; look at cost and turns instead.",
            "- Independence: trials of the same task are correlated, so treating pairs as "
            "independent understates uncertainty with few tasks. A task-clustered bootstrap is "
            f"used automatically for a series with at least {data.cluster_min_tasks} tasks.",
            f"- Multiple comparisons: {data.comparisons_shown} comparisons are shown "
            f"({by_metric}), re-evaluated daily. At 95%, roughly 1 in 20 will show a difference "
            "by chance. The aa-test row compares identical configurations: it is the noise-floor "
            "reference and should show none.",
            "- Subsets: the per-difficulty and injection lines are point rates over small subsets "
            "of the pairs, with no intervals; do not read a difference between tiers as "
            "significant.",
            "- Exclusions: a pair is left out when either attempt ended in `error` (an API or "
            "harness failure), was environment-tampered, or its other side never ran; the number "
            "is in parentheses. Truncated, refused, incomplete, max-turn and over-budget outcomes "
            "are agent outcomes: they stay in and count as failures unless the attempt passed.",
        ]
        if methods:
            lines.append("- Method: " + "; ".join(escape(m, 300) for m in methods) + ".")
        if data.unreadable:
            lines.append(f"- {data.unreadable} unreadable result line(s)/file(s) were ignored.")
        lines.append(
            "- Charts are SVGs that follow your OS/browser colour scheme, which can differ from "
            "GitHub's own theme setting."
        )
        return lines
