"""Renders video frames, optionally with the ROIs drawn on them, for visual inspection."""

import hashlib
from pathlib import Path

import cv2 as cv
import numpy as np

from planthz.db import data_dir
from planthz.rois import Roi

# Positions along the video, in tenths. A small fixed set keeps the frame cache bounded.
MAX_POSITION = 10

# Width of the frames kept for every position but the first, which are only looked at. At 1920×1080
# one takes about 27 KB instead of about 237 KB at full resolution.
REDUCED_WIDTH = 960

# BGR versions of the plant colors in static/style.css.
PLANT_COLORS = {1: (214, 120, 42), 2: (43, 98, 217)}


class FrameUnavailable(Exception):
    pass


def cache_path(video_path: Path, position: int, reduced: bool = False) -> Path:
    # Keyed by path, not video id: SQLite can reuse the id of a deleted campaign's video.
    key = hashlib.sha1(str(video_path).encode()).hexdigest()[:20]
    cache_dir = data_dir() / "frames"
    cache_dir.mkdir(exist_ok=True)

    return cache_dir / f"{key}_{position}{'_reduced' if reduced else ''}.jpg"


def _read_frame(video_path: Path, position: int) -> np.ndarray:
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

    return frame


def _encode(video_path: Path, frame: np.ndarray, quality: int) -> bytes:
    ok, encoded = cv.imencode(".jpg", frame, [cv.IMWRITE_JPEG_QUALITY, quality])

    if not ok:
        raise FrameUnavailable(f"Could not encode a frame of {video_path}")

    return encoded.tobytes()


def _decode(video_path: Path, jpeg: bytes) -> np.ndarray:
    frame = cv.imdecode(np.frombuffer(jpeg, np.uint8), cv.IMREAD_COLOR)

    if frame is None:
        raise FrameUnavailable(f"Could not decode the cached frame of {video_path}")

    return frame


def _resize(frame: np.ndarray, width: int) -> np.ndarray:
    scale = width / frame.shape[1]

    return cv.resize(frame, (width, round(frame.shape[0] * scale)), interpolation=cv.INTER_AREA)


def frame_jpeg(video_path: Path, position: int) -> tuple[bytes, float]:
    """Return the frame and its scale from video pixels, so ROIs can be drawn on it.

    The cache lets the frame outlive the video on disk: at full resolution, with a scale of 1, when
    it was cached or the video is on disk, and otherwise as the reduced frame `keep_frames` stored.
    """
    path = cache_path(video_path, position)

    if path.is_file():
        return path.read_bytes(), 1.0

    try:
        jpeg = _encode(video_path, _read_frame(video_path, position), 90)
    except FrameUnavailable:
        reduced = cache_path(video_path, position, reduced=True)

        if not reduced.is_file():
            raise

        return reduced.read_bytes(), _reduced_scale(video_path)

    store_frame(video_path, position, jpeg)

    return jpeg, 1.0


def _reduced_scale(video_path: Path) -> float:
    size = video_size(video_path)

    if size is None:
        raise FrameUnavailable(f"No full-resolution frame of {video_path} to place the ROIs on its reduced frames")

    return min(1.0, REDUCED_WIDTH / size[0])


def keep_frames(video_path: Path) -> None:
    """Cache the first frame at full resolution and every other position reduced, for when the video leaves the disk."""
    frame_jpeg(video_path, 0)

    for position in range(1, MAX_POSITION + 1):
        if cache_path(video_path, position).is_file() or cache_path(video_path, position, reduced=True).is_file():
            continue

        frame = _read_frame(video_path, position)

        if frame.shape[1] > REDUCED_WIDTH:
            frame = _resize(frame, REDUCED_WIDTH)

        store_frame(video_path, position, _encode(video_path, frame, 80), reduced=True)


def store_frame(video_path: Path, position: int, jpeg: bytes, reduced: bool = False) -> None:
    path = cache_path(video_path, position, reduced)
    temporary_path = path.with_suffix(f".{id(jpeg)}.tmp")
    temporary_path.write_bytes(jpeg)
    temporary_path.replace(path)


def render_frame(video_path: Path, position: int, rois: dict[int, Roi] | None, width: int | None) -> bytes:
    jpeg, scale = frame_jpeg(video_path, position)

    if not rois and width is None:
        return jpeg

    frame = _decode(video_path, jpeg)

    if width is not None and width < frame.shape[1]:
        scale *= width / frame.shape[1]
        frame = _resize(frame, width)

    for plant_id, roi in (rois or {}).items():
        _draw_roi(frame, plant_id, roi, scale)

    return _encode(video_path, frame, 85)


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


def video_size(video_path: Path) -> tuple[int, int] | None:
    """Return the width and height of the video, from the file or, once it left the disk, from its first frame."""
    size = frame_size(video_path)
    # The first frame is kept at full resolution, so its size is the video's.
    first = cache_path(video_path, 0)

    if size is not None or not first.is_file():
        return size

    height, width = _decode(video_path, first.read_bytes()).shape[:2]

    return width, height


def frame_size(video_path: Path) -> tuple[int, int] | None:
    video = cv.VideoCapture(str(video_path))

    try:
        if not video.isOpened():
            return None

        return int(video.get(cv.CAP_PROP_FRAME_WIDTH)), int(video.get(cv.CAP_PROP_FRAME_HEIGHT))
    finally:
        video.release()
