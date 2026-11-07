from pathlib import Path

import numpy as np
import pytest
from conftest import HEIGHT, WIDTH

from planthz.methodology import (
    N_PC,
    SCALE_OUT,
    compute_source_maps,
    dominant_source,
    estimate_dominant_frequency,
    highest_peak,
)
from planthz.rois import Roi

PLANT_ROIS = {1: Roi(0, 0, WIDTH // 2, HEIGHT), 2: Roi(WIDTH // 2, 0, WIDTH // 2, HEIGHT)}


def first_video(videos_dir: Path) -> Path:
    return videos_dir / "2026-10-29" / "test_20261029_120000.mp4"


# Values returned by marajo's scripts/bss_pipeline.py on this same video. They are not the true 3 and
# 5 Hz: the dominant source is chosen among sources of arbitrary scale, and that stays as published.
ORIGINAL_CODE_RESULTS = {1: (2.2, 7), 2: (1.8, 7)}


@pytest.mark.parametrize(("plant_id", "roi"), PLANT_ROIS.items())
def test_matches_the_original_code(videos_dir: Path, plant_id: int, roi: Roi) -> None:
    estimate = estimate_dominant_frequency(first_video(videos_dir), roi)

    assert (estimate.frequency, estimate.source) == pytest.approx(ORIGINAL_CODE_RESULTS[plant_id])


@pytest.mark.parametrize(("plant_id", "roi"), PLANT_ROIS.items())
def test_source_maps_pick_the_same_dominant_source(videos_dir: Path, plant_id: int, roi: Roi) -> None:
    maps = compute_source_maps(first_video(videos_dir), roi)

    assert maps.dominant == estimate_dominant_frequency(first_video(videos_dir), roi)
    assert maps.maps.shape == (N_PC, int(roi.h * SCALE_OUT), int(roi.w * SCALE_OUT))
    assert maps.signals.shape[1] == N_PC


def test_source_maps_take_the_size_the_encoder_wrote(videos_dir: Path) -> None:
    # A 0.2 scale gives 17x23, and the encoder rounds it down to even sizes.
    maps = compute_source_maps(first_video(videos_dir), Roi(0, 0, 87, 117))

    assert maps.maps.shape == (N_PC, 22, 16)


def test_roi_with_odd_size_still_works(videos_dir: Path) -> None:
    # A 0.2 scale gives 17x23 here, and the encoder only accepts even sizes.
    estimate = estimate_dominant_frequency(first_video(videos_dir), Roi(0, 0, 87, 117))

    assert estimate.source is not None


def test_roi_outside_the_frame_fails_instead_of_clipping(videos_dir: Path) -> None:
    with pytest.raises(ValueError, match="does not fit"):
        estimate_dominant_frequency(first_video(videos_dir), Roi(WIDTH // 2, 0, WIDTH, HEIGHT))


def test_unreadable_video_fails(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    with pytest.raises(OSError, match="Could not open"):
        estimate_dominant_frequency(broken, Roi(0, 0, 10, 10))


def test_highest_peak_finds_the_sine_frequency() -> None:
    time = np.arange(600) / 60
    frequency, power = highest_peak(np.sin(2 * np.pi * 2.5 * time), 60)

    assert frequency == pytest.approx(2.5, abs=0.1)
    assert power > 0


def test_dominant_source_ignores_peaks_at_or_below_the_threshold() -> None:
    estimate = dominant_source([(0.5, 100.0), (1.0, 90.0), (2.0, 10.0), (3.0, 20.0)])

    assert (estimate.frequency, estimate.amplitude, estimate.source) == (3.0, 20.0, 3)


def test_dominant_source_without_any_peak_has_no_frequency() -> None:
    estimate = dominant_source([(0.5, 100.0)])

    assert (estimate.frequency, estimate.amplitude, estimate.source) == (None, None, None)
