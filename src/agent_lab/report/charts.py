"""SVG charts, standard library only. Output depends only on the data (no clock, no randomness).

The SVGs have a transparent background and colours that read on light and dark pages, plus a
``prefers-color-scheme`` override. GitHub shows README SVGs as images, so that override follows
the viewer's OS/browser setting rather than GitHub's own theme toggle.
"""

import math
from datetime import date, timedelta
from xml.sax.saxutils import escape

from agent_lab.report.model import DayCost, Delta, SeriesReport

_STYLE = """<style>
  :root { --fg: #57606a; --grid: #afb8c1; --accent: #0969da; --band: rgba(140,149,159,0.18); }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #9198a1; --grid: #3d444d; --accent: #4493f8; --band: rgba(145,152,161,0.18); }
  }
  text { fill: var(--fg); font: 12px -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
  .small { font-size: 10px; }
  .head { font-weight: 600; }
  .axis, .zero { stroke: var(--grid); stroke-width: 1; }
  .zero { stroke-dasharray: 4 3; }
  .band { fill: var(--band); }
  .whisker { stroke: var(--accent); stroke-width: 2; }
  .point { fill: var(--accent); }
  .hollow { fill: none; stroke: var(--accent); stroke-width: 2; }
  .bar { fill: var(--accent); }
</style>"""


def _num(value: float, digits: int = 1) -> str:
    rounded = round(value, digits)
    return f"{(0.0 if rounded == 0 else rounded):.{digits}f}"


def _line(css: str, x1: float, y1: float, x2: float, y2: float) -> str:
    return f'<line class="{css}" x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}"/>'


def _text(css: str, x: float, y: float, text: str, anchor: str = "start") -> str:
    cls = f' class="{css}"' if css else ""
    return f'<text{cls} x="{_num(x)}" y="{_num(y)}" text-anchor="{anchor}">{escape(text)}</text>'


def _circle(css: str, cx: float, cy: float) -> str:
    return f'<circle class="{css}" cx="{_num(cx)}" cy="{_num(cy)}" r="4"/>'


def _svg(width: int, height: int, title: str, desc: str, body: list[str]) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-labelledby="title desc">',
            f'<title id="title">{escape(title)}</title>',
            f'<desc id="desc">{escape(desc)}</desc>',
            _STYLE,
            *body,
            "</svg>",
            "",
        ]
    )


class TracksChart:
    """One row per track (current series): delta pass rate and delta cost per attempt."""

    _WIDTH = 760
    _ROW = 30
    _TOP = 58
    _PANELS = ((170.0, 430.0), (500.0, 740.0))

    def render(self, series: list[SeriesReport]) -> str:
        rows = sorted(series, key=lambda s: s.track)
        height = self._TOP + self._ROW * len(rows) + 34
        body: list[str] = []
        for index, s in enumerate(rows):
            top = self._TOP + index * self._ROW
            if s.noise_floor:
                body.append(
                    f'<rect class="band" x="4" y="{top}" '
                    f'width="{self._WIDTH - 8}" height="{self._ROW}"/>'
                )
            label = s.track + (" (noise floor)" if s.noise_floor else "")
            body.append(_text("", 160, top + 19, label, "end"))
        pass_deltas = [s.delta_pass_pp for s in rows]
        cost_deltas = [s.delta_cost_usd for s in rows]
        body += self._panel(self._PANELS[0], "Delta pass rate (pp)", pass_deltas, 1, 1.0)
        body += self._panel(self._PANELS[1], "Delta cost per attempt (USD)", cost_deltas, 4, 0.001)
        desc = "; ".join(
            f"{s.track}: pass {_num(s.delta_pass_pp.mean)} pp, "
            f"cost {_num(s.delta_cost_usd.mean, 4)} USD"
            for s in rows
        )
        return _svg(
            self._WIDTH,
            height,
            "Variant minus baseline by track",
            f"Point estimates with 95% intervals, variant minus baseline. {desc}",
            body,
        )

    def _panel(
        self,
        span: tuple[float, float],
        name: str,
        deltas: list[Delta],
        digits: int,
        default: float,
    ) -> list[str]:
        x0, x1 = span
        mid, half = (x0 + x1) / 2, (x1 - x0) / 2
        limit = self._limit(deltas, default)
        top, bottom = self._TOP - 6, self._TOP + len(deltas) * self._ROW + 6

        def x(value: float) -> float:
            return mid + value / limit * half

        out = [
            _text("head", mid, 22, name, "middle"),
            _line("zero", mid, top, mid, bottom),
            _line("axis", x0, bottom, x1, bottom),
            _text("small", x0, bottom + 14, _num(-limit, digits)),
            _text("small", mid, bottom + 14, "0", "middle"),
            _text("small", x1, bottom + 14, _num(limit, digits), "end"),
        ]
        for index, delta in enumerate(deltas):
            cy = self._TOP + index * self._ROW + self._ROW / 2
            if delta.low is None or delta.high is None:
                out.append(_circle("hollow", x(delta.mean), cy))
                continue
            lo, hi = x(delta.low), x(delta.high)
            out += [
                _line("whisker", lo, cy, hi, cy),
                _line("whisker", lo, cy - 5, lo, cy + 5),
                _line("whisker", hi, cy - 5, hi, cy + 5),
                _circle("point", x(delta.mean), cy),
            ]
        return out

    @staticmethod
    def _limit(deltas: list[Delta], default: float) -> float:
        values = [abs(v) for d in deltas for v in (d.mean, d.low, d.high) if v is not None]
        largest = max(values) if values else 0.0
        return largest * 1.1 if largest > 0 else default


class DailySpendChart:
    """Bars of each day's total spend over the last calendar days, labelled by track."""

    _WIDTH = 760
    _HEIGHT = 250
    _LEFT, _RIGHT = 64.0, 744.0
    _BASE, _MAX_BAR = 190.0, 110.0
    _SHOWN_DAYS = 30

    def render(self, days: list[DayCost]) -> str:
        by_date = {d.date: d for d in days}
        last = date.fromisoformat(max(by_date))
        first = max(date.fromisoformat(min(by_date)), last - timedelta(days=self._SHOWN_DAYS - 1))
        count = (last - first).days + 1
        top = max((d.spend_usd for d in days if date.fromisoformat(d.date) >= first), default=0.0)
        scale = top * 1.05 if top > 0 else 1.0
        slot = (self._RIGHT - self._LEFT) / count
        bar = min(26.0, slot * 0.7)
        step = max(1, math.ceil(count / 10))
        body = [
            _line("axis", self._LEFT, self._BASE, self._RIGHT, self._BASE),
            _text("small", self._LEFT - 6, self._BASE + 4, "$0", "end"),
            _text("small", self._LEFT - 6, self._BASE - self._MAX_BAR + 4, f"${scale:.4f}", "end"),
        ]
        described: list[str] = []
        for offset in range(count):
            day = first + timedelta(days=offset)
            centre = self._LEFT + slot * (offset + 0.5)
            if offset % step == 0 or offset == count - 1:
                body.append(_text("small", centre, self._BASE + 16, f"{day:%m-%d}", "middle"))
            entry = by_date.get(day.isoformat())
            if entry is None:
                continue
            height = entry.spend_usd / scale * self._MAX_BAR
            y = self._BASE - height
            body += [
                f'<rect class="bar" x="{_num(centre - bar / 2)}" y="{_num(y)}" '
                f'width="{_num(bar)}" height="{_num(height)}"/>',
                f'<text class="small" transform="translate({_num(centre + 3)} {_num(y - 4)}) '
                f'rotate(-90)">{escape(entry.track[:14])}</text>',
            ]
            described.append(f"{entry.date} {entry.track} ${entry.spend_usd:.4f}")
        return _svg(
            self._WIDTH,
            self._HEIGHT,
            "Daily spend",
            "Total spend per day in USD (attempts and warm-ups), labelled by track. "
            + "; ".join(described),
            body,
        )
