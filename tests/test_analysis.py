import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from planthz import charts
from planthz.analysis import build_analysis, moving_average, results_csv
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

    chart = charts.frequency_per_video(analysis.rows)

    assert 'data-plant="1"' in chart
    assert 'data-plant="2"' in chart
    assert "/videos/1" in chart
    assert "Dia da captura" in chart
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


def test_daily_chart_shades_the_standard_deviation_within_the_axis() -> None:
    table = pd.DataFrame(
        {
            "day": ["2026-10-29", "2026-10-30"],
            "plant_id": [1, 1],
            "mean": [2.0, 3.0],
            "std": [1.0, None],
            "size": [5, 1],
        }
    )

    chart = charts.daily_means(table)

    assert chart.count('class="band plant-1"') == 1
    assert ">1,0</text>" in chart
    assert "Média: 2,000 Hz\nDesvio padrão: 1,000 Hz\nVídeos: 5" in chart
    assert "Desvio padrão: —" in chart


def test_daily_chart_moves_lines_and_markers_to_the_same_place_without_the_band() -> None:
    table = pd.DataFrame(
        {
            "day": ["2026-10-29", "2026-10-30"],
            "plant_id": [1, 1],
            "mean": [2.0, 3.0],
            "std": [1.5, 0.5],
            "size": [5, 5],
        }
    )

    chart = charts.daily_means(table)
    rescale = re.search(r"matrix\(1, 0, 0, ([\d.-]+), 0, ([\d.-]+)\)", chart)
    line = re.search(r'<polyline[^>]*points="([^"]*)"', chart)
    assert rescale and line
    scale, offset = float(rescale[1]), float(rescale[2])
    line_ys = [float(point.split(",")[1]) for point in line[1].split()]
    shifts = [float(shift) for shift in re.findall(r"--shift: ([\d.-]+)px", chart)]
    marker_ys = [float(y) for y in re.findall(r'<circle class="plant-1" cx="[\d.]+" cy="([\d.]+)"', chart)]

    assert scale != 1
    for line_y, marker_y, shift in zip(line_ys, marker_ys, shifts, strict=True):
        assert scale * line_y + offset == pytest.approx(marker_y + shift, abs=0.2)


def test_moving_average_uses_a_centred_time_window() -> None:
    rows = pd.DataFrame(
        {
            "captured_at": [datetime(2026, 10, 29, hour) for hour in (3, 0, 1, 2)],
            "frequency": [8.0, 2.0, 4.0, 6.0],
        },
        index=[13, 10, 11, 12],
    )

    means = moving_average(rows, 3)

    assert means.to_dict() == {10: 3.0, 11: 4.0, 12: 6.0, 13: 7.0}


def test_per_video_chart_breaks_its_lines_at_days_without_videos() -> None:
    runs = [
        make_run(video_id, plant_id, 2.0 + video_id, day=day)
        for video_id, day in enumerate(["2026-10-29", "2026-10-30", "2026-11-02"], start=1)
        for plant_id in (1, 2)
    ]

    chart = charts.frequency_per_video(build_analysis(runs).rows)

    assert chart.count('class="line raw plant-1"') == 2
    assert chart.count('class="line average plant-1" points') == 2 * 4
