from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from planthz import charts
from planthz.analysis import build_analysis, results_csv
from planthz.rois import Roi
from planthz.store import Result, Run, RunStatus, Video, run_status

ROI = Roi(0, 0, 10, 10)


def make_run(video_id: int, plant_id: int, frequency: float | None, day: str = "2026-10-29") -> Run:
    video = Video(
        video_id, 1, f"v{video_id}.mp4", Path(f"/v{video_id}.mp4"), day, datetime.fromisoformat(f"{day}T12:00:00"), True
    )
    result = Result(ROI, frequency, 1.0 if frequency else None, 0 if frequency else None, None, datetime(2026, 11, 1))

    return Run(video, plant_id, ROI, result, run_status(video, ROI, result))


def test_video_without_a_valid_result_for_both_plants_leaves_for_both() -> None:
    runs = [make_run(1, 1, 2.0), make_run(1, 2, 3.0), make_run(2, 1, 2.0), make_run(2, 2, None)]

    analysis = build_analysis(runs)

    assert set(analysis.rows["video_id"]) == {1}


def test_plant_summary_and_daily_means() -> None:
    runs = [
        make_run(1, 1, 2.0),
        make_run(1, 2, 3.0),
        make_run(2, 1, 4.0),
        make_run(2, 2, 5.0),
        make_run(3, 1, 6.0, day="2026-10-31"),
        make_run(3, 2, 7.0, day="2026-10-31"),
    ]

    analysis = build_analysis(runs)

    assert analysis.plant_summary.loc[1, "mean"] == pytest.approx(4.0)
    assert analysis.plant_summary.loc[2, "median"] == pytest.approx(5.0)
    assert analysis.plant_summary.loc[1, "size"] == 3

    day_one = analysis.daily_means[
        (analysis.daily_means["day"] == "2026-10-29") & (analysis.daily_means["plant_id"] == 1)
    ]
    assert day_one["mean"].item() == pytest.approx(3.0)


def test_stale_results_stay_out_of_the_analysis() -> None:
    stale = make_run(1, 1, 2.0)
    other_roi = Roi(1, 1, 5, 5)
    runs = [
        Run(stale.video, 1, other_roi, stale.result, run_status(stale.video, other_roi, stale.result)),
        make_run(1, 2, 3.0),
    ]

    assert runs[0].status is RunStatus.STALE
    assert build_analysis(runs).rows.empty


def test_analysis_without_results_is_empty() -> None:
    assert build_analysis([]).rows.empty


def test_csv_keeps_runs_left_out_of_the_analysis() -> None:
    lines = results_csv([make_run(1, 1, 2.0), make_run(1, 2, None)]).splitlines()

    assert lines[0].startswith("video,day,captured_at,plant_id,status")
    assert ",done,2.0," in lines[1]
    assert ",no_peak,," in lines[2]


def test_charts_handle_a_single_value() -> None:
    analysis = build_analysis([make_run(1, 1, 2.0), make_run(1, 2, 2.0)])

    panels = charts.frequency_per_video(analysis.rows)

    assert set(panels) == {1, 2}
    assert "/videos/1" in panels[1]
    assert "<svg" in charts.daily_means(analysis.daily_means)


def test_daily_chart_breaks_the_line_at_days_without_videos() -> None:
    table = pd.DataFrame(
        {
            "day": ["2026-10-29", "2026-10-30", "2026-11-02"],
            "plant_id": [1, 1, 1],
            "mean": [2.0, 3.0, 4.0],
            "std": [0.1, 0.1, 0.1],
            "size": [5, 5, 5],
        }
    )

    assert charts.daily_means(table).count("<polyline") == 2
