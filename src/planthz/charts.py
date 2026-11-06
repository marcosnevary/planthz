"""Inline SVG charts. Each point links to its video, so an odd value leads straight to its ROI."""

import math
from datetime import date, datetime, timedelta
from html import escape

import pandas as pd

from planthz.rois import PLANT_IDS

WIDTH = 1040
MARGIN = {"top": 16, "right": 16, "bottom": 30, "left": 52}
PLANT_CLASSES = {1: "plant-1", 2: "plant-2"}


def _nice_ticks(low: float, high: float, target: int = 5) -> list[float]:
    span = (high - low) or 1.0
    magnitude = 10 ** math.floor(math.log10(span / target))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= span / target)
    start = math.floor(low / step) * step
    count = max(math.ceil((high - start) / step), 1)

    return [round(start + i * step, 6) for i in range(count + 1)]


def _hz(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


class _Frame:
    """The axes of a chart: time on x, with one column per day, and frequency on y."""

    def __init__(self, first_day: date, last_day: date, y_ticks: list[float], height: int):
        self.start = datetime.combine(first_day, datetime.min.time())
        self.days = (last_day - first_day).days + 1
        self.y_ticks = y_ticks
        self.height = height

    def x(self, moment: datetime) -> float:
        fraction = (moment - self.start).total_seconds() / (self.days * 86400)

        return MARGIN["left"] + fraction * (WIDTH - MARGIN["left"] - MARGIN["right"])

    def y(self, value: float) -> float:
        low, high = self.y_ticks[0], self.y_ticks[-1]
        plot_height = self.height - MARGIN["top"] - MARGIN["bottom"]

        return MARGIN["top"] + (high - value) / (high - low) * plot_height

    def render(self) -> str:
        bottom = self.height - MARGIN["bottom"]
        parts = []

        for tick in self.y_ticks:
            y = self.y(tick)
            parts.append(
                f'<line class="grid" x1="{MARGIN["left"]}" x2="{WIDTH - MARGIN["right"]}" y1="{y:.1f}" y2="{y:.1f}"/>'
            )
            parts.append(
                f'<text class="tick" x="{MARGIN["left"] - 8}" y="{y + 4:.1f}" text-anchor="end">{_hz(tick, 1)}</text>'
            )

        # Labels are thinned so a long campaign does not collide, while day lines stay.
        label_every = max(1, math.ceil(self.days / 16))

        for offset in range(self.days + 1):
            x = self.x(self.start + timedelta(days=offset))
            parts.append(f'<line class="day-line" x1="{x:.1f}" x2="{x:.1f}" y1="{MARGIN["top"]}" y2="{bottom}"/>')

            if offset < self.days and offset % label_every == 0:
                noon = self.x(self.start + timedelta(days=offset, hours=12))
                label = (self.start + timedelta(days=offset)).strftime("%d/%m")
                parts.append(f'<text class="tick" x="{noon:.1f}" y="{bottom + 20}" text-anchor="middle">{label}</text>')

        return "".join(parts)


def _marker(plant_id: int, x: float, y: float, size: float = 3.5) -> str:
    # Shape repeats the plant identity, so telling them apart never depends on color alone.
    css_class = PLANT_CLASSES[plant_id]

    if plant_id == 1:
        return f'<circle class="{css_class}" cx="{x:.1f}" cy="{y:.1f}" r="{size}"/>'

    top, base, half = y - size * 1.2, y + size, size * 1.15

    return (
        f'<polygon class="{css_class}" points="{x:.1f},{top:.1f} {x + half:.1f},{base:.1f} {x - half:.1f},{base:.1f}"/>'
    )


def _svg(height: int, label: str, content: str) -> str:
    return f'<svg class="chart" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(label)}">{content}</svg>'


def _shared_axis(rows: pd.DataFrame, height: int) -> _Frame:
    days = pd.to_datetime(rows["day"])

    return _Frame(
        days.min().date(),
        days.max().date(),
        _nice_ticks(float(rows["frequency"].min()), float(rows["frequency"].max())),
        height,
    )


def frequency_per_video(rows: pd.DataFrame) -> dict[int, str]:
    # Same axes on both panels, so a difference between plants is not an autoscale effect.
    height = 240
    frame = _shared_axis(rows, height)
    panels = {}

    for plant_id in PLANT_IDS:
        points = []

        for row in rows[rows["plant_id"] == plant_id].to_dict("records"):
            x, y = frame.x(row["captured_at"]), frame.y(row["frequency"])
            title = f"{row['captured_at']:%d/%m %H:%M} · {_hz(row['frequency'])} Hz · {row['video']}"
            points.append(
                f'<a href="/videos/{row["video_id"]}"><title>{escape(title)}</title>'
                f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="8"/>{_marker(plant_id, x, y)}</a>'
            )

        panels[plant_id] = _svg(
            height, f"Frequência dominante por vídeo, Planta {plant_id}", frame.render() + "".join(points)
        )

    return panels


def daily_means(table: pd.DataFrame) -> str:
    height = 280
    frame = _shared_axis(table.rename(columns={"mean": "frequency"}), height)
    parts = [frame.render()]

    for plant_id in PLANT_IDS:
        points = [
            (datetime.fromisoformat(row["day"]) + timedelta(hours=12), row["mean"], row["size"], row["day"])
            for row in table[table["plant_id"] == plant_id].to_dict("records")
        ]

        if not points:
            continue

        segment: list[str] = []
        previous_day: datetime | None = None

        # A day without videos breaks the line instead of being bridged as if there were data.
        for moment, value, _, _ in points:
            if previous_day is not None and moment - previous_day > timedelta(days=1):
                parts.append(f'<polyline class="line {PLANT_CLASSES[plant_id]}" points="{" ".join(segment)}"/>')
                segment = []

            segment.append(f"{frame.x(moment):.1f},{frame.y(value):.1f}")
            previous_day = moment

        parts.append(f'<polyline class="line {PLANT_CLASSES[plant_id]}" points="{" ".join(segment)}"/>')

        for moment, value, size, day in points:
            x, y = frame.x(moment), frame.y(value)
            title = f"Planta {plant_id} · {day} · {_hz(value)} Hz · {size} vídeo{'s' if size != 1 else ''}"
            parts.append(
                f'<g><title>{escape(title)}</title><circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="10"/>'
                f"{_marker(plant_id, x, y, 4.5)}</g>"
            )

    return _svg(height, "Frequência dominante média por dia e planta", "".join(parts))
