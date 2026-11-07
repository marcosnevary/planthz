"""Caches the source maps of a run and renders them for the video page."""

import hashlib
from pathlib import Path

import cv2 as cv
import numpy as np

from planthz.db import data_dir
from planthz.methodology import SourceMaps, compute_source_maps
from planthz.rois import Roi


class SourceMapsUnavailable(Exception):
    pass


def _cache_path(video_path: Path, roi: Roi) -> Path:
    # Keyed by path, not video id, for the same reason as the frame cache.
    key = hashlib.sha1(f"{video_path}|{roi}".encode()).hexdigest()[:20]
    cache_dir = data_dir() / "source_maps"
    cache_dir.mkdir(exist_ok=True)

    return cache_dir / f"{key}.npz"


def source_maps(video_path: Path, roi: Roi) -> SourceMaps:
    """Return the maps of the run with this ROI. The cache lets them outlive the video on disk."""
    cache_path = _cache_path(video_path, roi)

    if cache_path.is_file():
        with np.load(cache_path) as cached:
            return SourceMaps(
                maps=cached["maps"],
                signals=cached["signals"],
                peaks=[(float(frequency), float(power)) for frequency, power in cached["peaks"]],
                fps=float(cached["fps"]),
            )

    if not video_path.is_file():
        raise SourceMapsUnavailable(f"{video_path} is not on disk")

    try:
        computed = compute_source_maps(video_path, roi)
    except (OSError, ValueError) as error:
        raise SourceMapsUnavailable(str(error)) from error

    temporary_path = cache_path.with_suffix(f".{id(computed)}.tmp.npz")
    np.savez(
        temporary_path,
        maps=computed.maps.astype(np.float32),
        signals=computed.signals.astype(np.float32),
        peaks=np.asarray(computed.peaks),
        fps=computed.fps,
    )
    temporary_path.replace(cache_path)

    return computed


def map_png(maps: SourceMaps, source: int) -> bytes:
    """Return the absolute weight of the source on each pixel, normalized to 0–255, as a grayscale PNG."""
    weight = np.abs(maps.maps[source])
    weight = weight / (weight.max() + 1e-12)
    ok, encoded = cv.imencode(".png", np.round(weight * 255).astype(np.uint8))

    if not ok:
        raise SourceMapsUnavailable("Could not encode the source map")

    return encoded.tobytes()


def normalized_signal(maps: SourceMaps, source: int) -> list[float]:
    """Return |signal| scaled to 0–1, the per-frame intensity of marajo's dynamic overlay."""
    signal = np.abs(maps.signals[:, source])
    signal = signal / (signal.max() + 1e-12)

    return [round(float(value), 3) for value in signal]
