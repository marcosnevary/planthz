"""Turns the runs of a campaign into the tables shown on the analysis page."""

import csv
import io
from dataclasses import dataclass

import pandas as pd

from planthz.store import Run, RunStatus


@dataclass(frozen=True)
class Analysis:
    rows: pd.DataFrame
    plant_summary: pd.DataFrame
    daily_means: pd.DataFrame
    n_videos: int
    n_unpaired_videos: int


def valid_rows(runs: list[Run]) -> tuple[pd.DataFrame, int]:
    """Return the rows of the videos with a result for both plants, and how many videos were dropped.

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
    n_unpaired = rows.loc[~paired, "video_id"].nunique()

    return rows[paired], n_unpaired


def build_analysis(runs: list[Run]) -> Analysis:
    rows, n_unpaired = valid_rows(runs)

    return Analysis(
        rows=rows,
        plant_summary=rows.groupby("plant_id")["frequency"].agg(["mean", "median", "std", "size"]),
        daily_means=(
            rows.groupby(["day", "plant_id"])["frequency"].agg(["mean", "std", "size"]).reset_index().sort_values("day")
        ),
        n_videos=rows["video_id"].nunique(),
        n_unpaired_videos=n_unpaired,
    )


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
