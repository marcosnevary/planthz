from pathlib import Path

import cv2 as cv
import numpy as np
import pytest

from planthz import frames
from planthz.rois import Roi


def write_wide_video(path: Path, width: int = 1920, height: int = 1080) -> None:
    writer = cv.VideoWriter(str(path), cv.VideoWriter.fourcc(*"mp4v"), 30.0, (width, height))

    for _ in range(11):
        writer.write(np.full((height, width, 3), 128, np.uint8))

    writer.release()


def decode(jpeg: bytes) -> np.ndarray:
    frame = cv.imdecode(np.frombuffer(jpeg, np.uint8), cv.IMREAD_COLOR)
    assert frame is not None

    return frame


def test_reduced_frames_replace_the_video_once_it_leaves_the_disk(tmp_path: Path) -> None:
    video = tmp_path / "wide.mp4"
    write_wide_video(video)
    frames.keep_frames(video)
    video.unlink()

    plain = decode(frames.render_frame(video, 5, None, None))
    assert plain.shape[1] == frames.REDUCED_WIDTH

    # A ROI at x = 1000 in video pixels lands at x = 500 on the reduced frame.
    rois = {1: Roi(1000, 200, 400, 400)}
    drawn = decode(frames.render_frame(video, 5, rois, None))
    column = np.abs(drawn.astype(int) - plain.astype(int)).sum(axis=(0, 2))
    assert column[:490].sum() == pytest.approx(0, abs=50_000)
    assert column[495:505].sum() > 0

    assert frames.render_frame(video, 0, None, None)


def test_without_any_kept_frame_the_frame_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(frames.FrameUnavailable):
        frames.render_frame(tmp_path / "gone.mp4", 5, None, None)
