"""Turning the dashboard's numbers into something a template can draw.

The geometry is computed here rather than in Jinja. Arithmetic inside a template
is where charts go wrong quietly -- an off-by-one in a loop index becomes a bar
in the wrong place, and nothing type-checks it.

The specs are fixed and not up for adjustment per chart: columns capped at 24px
with a 4px rounded cap and a 2px gap to their neighbour, a 2px line with round
joins, an 8px end marker carrying a 2px ring in the surface colour, and hairline
solid gridlines a step off the surface. One axis per chart, always. Every mark
carries a ``<title>`` so hovering says what it is without a line of JavaScript.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from graded_reader.dashboard import Dashboard

#: The drawing area. The SVG scales to its container; these are its own units.
WIDTH = 720.0
HEIGHT = 190.0
PAD_LEFT = 34.0
PAD_RIGHT = 10.0
PAD_TOP = 12.0
PAD_BOTTOM = 26.0

#: Mark specs, from the house chart rules.
MAX_COLUMN = 24.0
COLUMN_GAP = 2.0
CAP_RADIUS = 4.0
MARKER_RADIUS = 4.5

#: How tall a day with no reviews is drawn. Tall enough to see the row of days.
ZERO_STUB = 3.0

PLOT_WIDTH = WIDTH - PAD_LEFT - PAD_RIGHT
PLOT_HEIGHT = HEIGHT - PAD_TOP - PAD_BOTTOM


class Column(BaseModel):
    """One bar, plus the invisible rectangle that makes it easy to hover.

    The bar is a path rather than a rectangle because only the data end is
    rounded -- a rounded baseline would lift the bar off its own axis. Drawing
    it as one mark instead of a rounded rect with a square one patched over it
    keeps the geometry honest: the gap to the neighbour is the distance between
    two marks, not between four.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    hit_x: float
    hit_width: float
    title: str
    zero: bool = False


class Tick(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    x: float = 0.0
    y: float = 0.0
    anchor: str = "middle"


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float
    y: float
    title: str


class ColumnChart(BaseModel):
    """A column chart of one series."""

    model_config = ConfigDict(extra="forbid")

    columns: list[Column] = Field(default_factory=list)
    y_ticks: list[Tick] = Field(default_factory=list)
    x_ticks: list[Tick] = Field(default_factory=list)
    baseline: float = HEIGHT - PAD_BOTTOM
    empty: bool = True


class LineChart(BaseModel):
    """A line chart of one series, with its end labelled."""

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    area: str = ""
    markers: list[Marker] = Field(default_factory=list)
    y_ticks: list[Tick] = Field(default_factory=list)
    x_ticks: list[Tick] = Field(default_factory=list)
    end_label: str = ""
    end_x: float = 0.0
    end_y: float = 0.0
    empty: bool = True


def _column_path(x: float, y: float, width: float, height: float) -> str:
    """A bar with a rounded data end and a square foot on the baseline."""
    radius = min(CAP_RADIUS, width / 2, height)
    bottom = y + height
    return (
        f"M {x:.1f} {bottom:.1f} V {y + radius:.1f} "
        f"Q {x:.1f} {y:.1f} {x + radius:.1f} {y:.1f} "
        f"H {x + width - radius:.1f} "
        f"Q {x + width:.1f} {y:.1f} {x + width:.1f} {y + radius:.1f} "
        f"V {bottom:.1f} Z"
    )


def _x_ticks(labels: list[str], positions: list[float]) -> list[Tick]:
    """Label the ends and the middle, anchored so nothing leaves the frame.

    A tick centred on the last column would hang past the right edge by half a
    label. Anchoring the first to its start and the last to its end keeps the
    text inside the viewBox without moving the mark it belongs to.
    """
    ticks: list[Tick] = []
    for index, (label, x) in enumerate(zip(labels, positions, strict=True)):
        if index == 0:
            anchor = "start"
        elif index == len(labels) - 1:
            anchor = "end"
        else:
            anchor = "middle"
        ticks.append(Tick(value=label, x=x, anchor=anchor))
    return ticks


def _nice_ceiling(value: int) -> int:
    """A round number at or above ``value``, so the axis reads 0 / 5 / 10."""
    if value <= 5:
        return 5
    for step in (10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if value <= step:
            return step
    return ((value + 999) // 1000) * 1000


def history_chart(dashboard: Dashboard) -> ColumnChart:
    """Reviews per day over the window just past."""
    days = dashboard.history
    if not days:
        return ColumnChart()

    top = _nice_ceiling(dashboard.busiest_day)
    slot = PLOT_WIDTH / len(days)
    bar_width = min(MAX_COLUMN, max(2.0, slot - COLUMN_GAP))

    columns: list[Column] = []
    for index, entry in enumerate(days):
        hit_x = PAD_LEFT + index * slot
        centre = hit_x + slot / 2
        height = (entry.count / top) * PLOT_HEIGHT if top else 0.0
        # A day with nothing still gets a stub. Thirty invisible days and one
        # bar reads as a broken chart; thirty visible stubs and one bar reads as
        # a month in which you studied once, which is the truth.
        drawn = max(height, ZERO_STUB) if entry.count == 0 else height
        columns.append(
            Column(
                path=_column_path(
                    centre - bar_width / 2, HEIGHT - PAD_BOTTOM - drawn, bar_width, drawn
                ),
                hit_x=hit_x,
                hit_width=slot,
                title=f"{entry.label}: {entry.count} review{'s' if entry.count != 1 else ''}",
                zero=entry.count == 0,
            )
        )

    y_ticks = [
        Tick(value=str(int(top * fraction)), y=HEIGHT - PAD_BOTTOM - fraction * PLOT_HEIGHT)
        for fraction in (0.0, 0.5, 1.0)
    ]
    # Every day labelled is unreadable at this width; the ends and the middle
    # carry the range and the tooltip carries the rest.
    marks = sorted({0, len(days) // 2, len(days) - 1})
    x_ticks = _x_ticks(
        [days[index].label for index in marks],
        [PAD_LEFT + index * slot + slot / 2 for index in marks],
    )

    return ColumnChart(
        columns=columns,
        y_ticks=y_ticks,
        x_ticks=x_ticks,
        empty=dashboard.reviewed_in_window == 0,
    )


def retention_chart(dashboard: Dashboard) -> LineChart:
    """The deck's average chance of recall over the window ahead.

    The axis runs the full 0 to 100 per cent. Retention is a probability and
    that is its range; cropping the axis to make a gentle decline look like a
    cliff is the oldest trick in the book and this panel is meant to be trusted.
    """
    points = dashboard.retention
    if not points or all(point.retention == 0 for point in points):
        return LineChart()

    step = PLOT_WIDTH / max(1, len(points) - 1)
    coords = [
        (
            PAD_LEFT + index * step,
            PAD_TOP + (1.0 - point.retention) * PLOT_HEIGHT,
        )
        for index, point in enumerate(points)
    ]

    path = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)
    first_x, _ = coords[0]
    last_x, last_y = coords[-1]
    baseline = HEIGHT - PAD_BOTTOM
    area = f"{path} L {last_x:.1f} {baseline:.1f} L {first_x:.1f} {baseline:.1f} Z"

    markers = [
        Marker(
            x=x,
            y=y,
            title=f"{points[index].day.strftime('%d/%m')}: "
            f"{points[index].retention * 100:.0f}% recall, {points[index].due} due",
        )
        for index, (x, y) in enumerate(coords)
        if index % 5 == 0 or index == len(coords) - 1
    ]

    y_ticks = [
        Tick(value=f"{int(fraction * 100)}%", y=PAD_TOP + (1.0 - fraction) * PLOT_HEIGHT)
        for fraction in (0.0, 0.5, 1.0)
    ]
    marks = sorted({0, len(points) // 2, len(points) - 1})
    x_ticks = _x_ticks(
        [points[index].day.strftime("%d/%m") for index in marks],
        [PAD_LEFT + index * step for index in marks],
    )

    return LineChart(
        path=path,
        area=area,
        markers=markers,
        y_ticks=y_ticks,
        x_ticks=x_ticks,
        end_label=f"{points[-1].retention * 100:.0f}%",
        end_x=last_x,
        end_y=last_y,
        empty=False,
    )
