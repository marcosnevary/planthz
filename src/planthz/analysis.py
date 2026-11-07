"""Turns the runs of a campaign into the tables shown on the analysis page."""

import csv
import io
from dataclasses import dataclass

import pandas as pd

from planthz.store import Run, RunStatus

# Windows offered for the moving average on the per-video chart. Videos are usually 30 minutes apart.
MOVING_AVERAGE_HOURS = (1, 3, 6, 24)


@dataclass(frozen=True)
class Analysis:
    rows: pd.DataFrame
    plant_summary: pd.DataFrame
    daily_means: pd.DataFrame
    video_count: int


def valid_rows(runs: list[Run]) -> pd.DataFrame:
    """Return the rows of the videos with a result for both plants.

    The plants are compared on the same videos, so a video without a valid result for one plant
    leaves for both.
    """
    rows = pd.DataFrame(
        [
            {
                "video_id": run.video.id,
                "video": run.video.name,
                "day": run.video.day,
                "captured_at": run.video.captured_at,
                "plant_id": run.plant_id,
                "frequency": run.result.frequency,
            }
            for run in runs
            if run.status is RunStatus.DONE and run.result is not None
        ],
        columns=["video_id", "video", "day", "captured_at", "plant_id", "frequency"],
    )
    n_plants = len({run.plant_id for run in runs})
    paired = rows.groupby("video_id")["plant_id"].transform("nunique").eq(n_plants)

    return rows[paired]


def build_analysis(runs: list[Run]) -> Analysis:
    rows = valid_rows(runs)

    return Analysis(
        rows=rows,
        plant_summary=rows.groupby("plant_id")["frequency"].agg(["mean", "median", "std", "size"]),
        daily_means=(
            rows.groupby(["day", "plant_id"])["frequency"].agg(["mean", "std", "size"]).reset_index().sort_values("day")
        ),
        video_count=rows["video_id"].nunique(),
    )


def moving_average(rows: pd.DataFrame, hours: int) -> pd.Series:
    """Return the mean frequency over a window of `hours` centred on each video, for the rows of one plant.

    The window is in time rather than in videos, so a missing video narrows the sample instead of widening the period.
    """
    ordered = rows.sort_values("captured_at")
    frequencies = pd.Series(ordered["frequency"].to_numpy(), index=pd.to_datetime(ordered["captured_at"]))
    means = frequencies.rolling(pd.Timedelta(hours=hours), center=True).mean()

    return pd.Series(means.to_numpy(), index=ordered.index)


def results_csv(runs: list[Run]) -> str:
    """Export every run, including those left out of the analysis, with the reason in `status`."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["video", "day", "captured_at", "plant_id", "status", "frequency", "amplitude", "source", "roi", "error"]
    )

    for run in runs:
        result = run.result
        writer.writerow(
            [
                run.video.name,
                run.video.day,
                run.video.captured_at.isoformat(),
                run.plant_id,
                run.status.value,
                result.frequency if result else "",
                result.amplitude if result else "",
                result.source if result else "",
                str(result.roi) if result else "",
                result.error or "" if result else "",
            ]
        )

    return output.getvalue()
