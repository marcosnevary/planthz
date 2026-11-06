"""Renders video frames, optionally with the ROIs drawn on them, for visual inspection."""

import hashlib
from pathlib import Path

import cv2 as cv
import numpy as np

from planthz.db import data_dir
from planthz.rois import Roi

# Positions along the video, in tenths. A small fixed set keeps the frame cache bounded.
MAX_POSITION = 10

# BGR versions of the plant colors in static/style.css.
PLANT_COLORS = {1: (214, 120, 42), 2: (43, 98, 217)}


class FrameUnavailable(Exception):
    pass


def _cache_path(video_path: Path, position: int) -> Path:
    # Keyed by path, not video id: SQLite can reuse the id of a deleted campaign's video.
    key = hashlib.sha1(str(video_path).encode()).hexdigest()[:20]
    cache_dir = data_dir() / "frames"
    cache_dir.mkdir(exist_ok=True)

    return cache_dir / f"{key}_{position}.jpg"


def _extract_frame(video_path: Path, position: int) -> bytes:
    video = cv.VideoCapture(str(video_path))

    if not video.isOpened():
        raise FrameUnavailable(f"Could not open {video_path}")

    try:
        last_frame = max(int(video.get(cv.CAP_PROP_FRAME_COUNT)) - 1, 0)
        video.set(cv.CAP_PROP_POS_FRAMES, round(last_frame * position / MAX_POSITION))
        ok, frame = video.read()
    finally:
        video.release()

    if not ok:
        raise FrameUnavailable(f"Could not read a frame of {video_path}")

    ok, encoded = cv.imencode(".jpg", frame, [cv.IMWRITE_JPEG_QUALITY, 90])

    if not ok:
        raise FrameUnavailable(f"Could not encode a frame of {video_path}")

    return encoded.tobytes()


def frame_jpeg(video_path: Path, position: int) -> bytes:
    """Return the frame at full resolution. The cache lets it outlive the video on disk."""
    cache_path = _cache_path(video_path, position)

    if cache_path.is_file():
        return cache_path.read_bytes()

    jpeg = _extract_frame(video_path, position)
    temporary_path = cache_path.with_suffix(f".{id(jpeg)}.tmp")
    temporary_path.write_bytes(jpeg)
    temporary_path.replace(cache_path)

    return jpeg


def render_frame(video_path: Path, position: int, rois: dict[int, Roi] | None, width: int | None) -> bytes:
    jpeg = frame_jpeg(video_path, position)

    if not rois and width is None:
        return jpeg

    frame = cv.imdecode(np.frombuffer(jpeg, np.uint8), cv.IMREAD_COLOR)

    if frame is None:
        raise FrameUnavailable(f"Could not decode the cached frame of {video_path}")

    scale = 1.0

    if width is not None and width < frame.shape[1]:
        scale = width / frame.shape[1]
        frame = cv.resize(frame, (width, round(frame.shape[0] * scale)), interpolation=cv.INTER_AREA)

    for plant_id, roi in (rois or {}).items():
        _draw_roi(frame, plant_id, roi, scale)

    ok, encoded = cv.imencode(".jpg", frame, [cv.IMWRITE_JPEG_QUALITY, 85])

    if not ok:
        raise FrameUnavailable(f"Could not encode a frame of {video_path}")

    return encoded.tobytes()


def _draw_roi(frame: np.ndarray, plant_id: int, roi: Roi, scale: float) -> None:
    color = PLANT_COLORS[plant_id]
    left, top = round(roi.x * scale), round(roi.y * scale)
    right, bottom = round((roi.x + roi.w) * scale), round((roi.y + roi.h) * scale)
    thickness = max(2, round(4 * scale))

    cv.rectangle(frame, (left, top), (right, bottom), color, thickness)

    label = f"Planta {plant_id}"
    font_scale = max(0.45, 1.1 * scale)
    (label_width, label_height), baseline = cv.getTextSize(label, cv.FONT_HERSHEY_SIMPLEX, font_scale, 1)
    padding = max(3, round(8 * scale))

    cv.rectangle(
        frame,
        (left, top),
        (left + label_width + 2 * padding, top + label_height + baseline + 2 * padding),
        color,
        cv.FILLED,
    )
    cv.putText(
        frame,
        label,
        (left + padding, top + label_height + padding),
        cv.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),
        1,
        cv.LINE_AA,
    )


def frame_size(video_path: Path) -> tuple[int, int] | None:
    video = cv.VideoCapture(str(video_path))

    try:
        if not video.isOpened():
            return None

        return int(video.get(cv.CAP_PROP_FRAME_WIDTH)), int(video.get(cv.CAP_PROP_FRAME_HEIGHT))
    finally:
        video.release()
