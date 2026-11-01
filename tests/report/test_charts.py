import xml.etree.ElementTree as ET

from report_support import delta, series

from agent_lab.report.charts import DailySpendChart, TracksChart
from agent_lab.report.model import DayCost

NS = {"svg": "http://www.w3.org/2000/svg"}


def parse(text: str) -> ET.Element:
    return ET.fromstring(text)  # raises on anything that is not well-formed XML


def texts(root: ET.Element) -> list[str]:
    return ["".join(t.itertext()) for t in root.iter("{http://www.w3.org/2000/svg}text")]


def two_rows() -> str:
    return TracksChart().render(
        [
            series(track="tool-design"),
            series(
                track="aa-test",
                noise_floor=True,
                delta_pass_pp=delta(0.0, 24.0),
                delta_cost_usd=delta(0.0, 0.0003),
            ),
        ]
    )


def test_the_tracks_chart_is_well_formed_and_accessible() -> None:
    root = parse(two_rows())
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.find("svg:title", NS).text == "Variant minus baseline by track"  # type: ignore[union-attr]
    assert "aa-test: pass 0.0 pp" in root.find("svg:desc", NS).text  # type: ignore[union-attr]
    assert root.get("role") == "img" and root.get("aria-labelledby") == "title desc"
    assert root.get("viewBox") == f"0 0 {root.get('width')} {root.get('height')}"


def test_the_tracks_chart_has_one_row_per_track_and_marks_the_reference() -> None:
    root = parse(two_rows())
    labels = texts(root)
    assert "aa-test (noise floor)" in labels and "tool-design" in labels
    assert labels.index("aa-test (noise floor)") < labels.index("tool-design")  # sorted by name
    assert len(root.findall(".//svg:rect[@class='band']", NS)) == 1  # only the reference row
    assert len(root.findall(".//svg:circle[@class='point']", NS)) == 4  # two metrics x two rows


def test_both_panels_have_a_zero_line_and_whiskers() -> None:
    root = parse(two_rows())
    assert len(root.findall(".//svg:line[@class='zero']", NS)) == 2
    assert len(root.findall(".//svg:line[@class='whisker']", NS)) == 4 * 3  # bar and two caps each
    assert {"Delta pass rate (pp)", "Delta cost per attempt (USD)"} <= set(texts(root))


def test_the_zero_line_is_where_zero_is() -> None:
    root = parse(two_rows())
    zero = float(root.find(".//svg:line[@class='zero']", NS).get("x1"))  # type: ignore[union-attr]
    symmetric = [
        c
        for c in root.findall(".//svg:circle[@class='point']", NS)
        if abs(float(c.get("cx")) - zero) < 0.1  # type: ignore[arg-type]
    ]
    assert symmetric  # the aa-test point estimate (0.0) sits exactly on the zero line


def test_insufficient_data_is_a_hollow_marker_without_whiskers() -> None:
    weak = series(delta_pass_pp=delta(10.0, None), delta_cost_usd=delta(0.001, None))
    root = parse(TracksChart().render([weak]))
    assert len(root.findall(".//svg:circle[@class='hollow']", NS)) == 2
    assert not root.findall(".//svg:line[@class='whisker']", NS)


def test_the_svgs_work_on_light_and_dark_pages() -> None:
    for text in (
        two_rows(),
        DailySpendChart().render([DayCost(date="2026-11-01", track="t", spend_usd=0.1)]),
    ):
        root = parse(text)
        style = root.find("svg:style", NS).text  # type: ignore[union-attr]
        assert "prefers-color-scheme: dark" in style and "var(--fg)" in style
        background = [r for r in root.findall(".//svg:rect", NS) if r.get("class") != "bar"]
        assert all(r.get("class") == "band" for r in background)  # nothing opaque behind the chart
        assert "background" not in text.split("</style>")[1]


def test_hostile_track_names_cannot_inject_markup() -> None:
    hostile = '</text><script>alert(1)</script><text x="'
    root = parse(TracksChart().render([series(track=hostile)]))
    assert not list(root.iter("{http://www.w3.org/2000/svg}script"))
    assert hostile in texts(root)  # shown as text, escaped
    days = [DayCost(date="2026-11-01", track=hostile, spend_usd=0.1)]
    daily = parse(DailySpendChart().render(days))
    assert not list(daily.iter("{http://www.w3.org/2000/svg}script"))


def test_the_charts_are_deterministic() -> None:
    assert two_rows() == two_rows()


def days(*entries: tuple[str, str, float]) -> list[DayCost]:
    return [DayCost(date=d, track=t, spend_usd=s) for d, t, s in entries]


def test_the_daily_chart_has_a_bar_per_day_labelled_with_its_track() -> None:
    root = parse(
        DailySpendChart().render(
            days(("2026-11-02", "model-comparison", 0.06), ("2026-11-04", "plan-first", 0.03))
        )
    )
    assert len(root.findall(".//svg:rect[@class='bar']", NS)) == 2
    labels = texts(root)
    assert "model-comparison"[:14] in labels and "plan-first" in labels
    assert {"11-02", "11-03", "11-04"} <= set(labels)  # the calendar, including the empty day
    assert "$0" in labels and "$0.0630" in labels  # the axis top is 1.05 x the largest spend


def test_a_taller_spend_is_a_taller_bar() -> None:
    root = parse(
        DailySpendChart().render(days(("2026-11-02", "a", 0.06), ("2026-11-03", "b", 0.03)))
    )
    heights = [float(r.get("height")) for r in root.findall(".//svg:rect[@class='bar']", NS)]  # type: ignore[arg-type]
    assert heights[0] > heights[1] > 0 and heights[0] / heights[1] == __import__("pytest").approx(
        2.0, rel=0.01
    )


def test_only_the_last_thirty_days_are_drawn() -> None:
    entries = [(f"2026-10-{d:02d}", "t", 0.01) for d in range(1, 32)] + [("2026-11-05", "t", 0.02)]
    root = parse(DailySpendChart().render(days(*entries)))
    # the window is Oct 7 .. Nov 5: 25 October days plus Nov 5
    assert len(root.findall(".//svg:rect[@class='bar']", NS)) == 26
    assert "10-07" in texts(root) and "10-06" not in texts(root)


def test_a_single_day_and_zero_spend_still_draw() -> None:
    root = parse(DailySpendChart().render(days(("2026-11-02", "t", 0.0))))
    assert len(root.findall(".//svg:rect[@class='bar']", NS)) == 1
