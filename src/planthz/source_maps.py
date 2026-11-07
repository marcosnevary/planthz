"""Stores the source maps of each run and renders them for the video page.

Only what the page shows is stored, in 8 bits: about 110 KB per run instead of 1.5 MB in float32.
"""

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import cv2 as cv
import numpy as np

from planthz.db import data_dir
from planthz.methodology import N_PC, SourceMaps, compute_source_maps, dominant_source
from planthz.rois import Roi


class SourceMapsUnavailable(Exception):
    pass


@dataclass(frozen=True)
class StoredMaps:
    """`weights` has shape (sources, height, width) and `intensities` (frames, sources). Both hold the
    absolute value scaled to 0–255 per source, since sign and scale of a source are arbitrary."""

    weights: np.ndarray
    intensities: np.ndarray
    peaks: list[tuple[float, float]]
    fps: float

    @property
    def dominant(self) -> int | None:
        return dominant_source(self.peaks).source


def _to_bytes(values: np.ndarray, axis: tuple[int, ...] | int) -> np.ndarray:
    magnitude = np.abs(values)
    magnitude = magnitude / (magnitude.max(axis=axis, keepdims=True) + 1e-12)

    return np.round(magnitude * 255).astype(np.uint8)


def cache_path(video_path: Path, roi: Roi) -> Path:
    # Keyed by path, not video id, for the same reason as the frame cache. An import keeps the path,
    # so the maps it brings land under the same key.
    key = hashlib.sha1(f"{video_path}|{roi}".encode()).hexdigest()[:20]
    cache_dir = data_dir() / "source_maps"
    cache_dir.mkdir(exist_ok=True)

    return cache_dir / f"{key}.npz"


def save(video_path: Path, roi: Roi, maps: SourceMaps) -> None:
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        weights=_to_bytes(maps.maps, axis=(1, 2)),
        intensities=_to_bytes(maps.signals, axis=0),
        peaks=np.asarray(maps.peaks, dtype=np.float64),
        fps=np.float64(maps.fps),
    )
    write(video_path, roi, buffer.getvalue())


def write(video_path: Path, roi: Roi, content: bytes) -> None:
    path = cache_path(video_path, roi)
    temporary_path = path.with_suffix(f".{id(content)}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)


def parse(content: bytes) -> StoredMaps:
    """Read stored maps, refusing anything the page could not draw."""
    try:
        with np.load(io.BytesIO(content), allow_pickle=False) as stored:
            weights, intensities, peaks, fps = (stored[key] for key in ("weights", "intensities", "peaks", "fps"))
    except (OSError, ValueError, KeyError, EOFError) as error:
        raise SourceMapsUnavailable("Damaged source maps") from error

    valid = (
        weights.dtype == np.uint8
        and weights.ndim == 3
        and weights.shape[0] == N_PC
        and intensities.dtype == np.uint8
        and intensities.ndim == 2
        and intensities.shape[1] == N_PC
        and peaks.shape == (N_PC, 2)
        and np.isfinite(peaks).all()
        and fps.shape == ()
        and np.isfinite(fps)
        and fps > 0
    )

    if not valid:
        raise SourceMapsUnavailable("Damaged source maps")

    return StoredMaps(
        weights=weights,
        intensities=intensities,
        peaks=[(float(frequency), float(power)) for frequency, power in peaks],
        fps=float(fps),
    )


def stored_maps(video_path: Path, roi: Roi) -> StoredMaps:
    """Return the maps of the run with this ROI, computing them if the video is still on disk."""
    path = cache_path(video_path, roi)

    if not path.is_file():
        if not video_path.is_file():
            raise SourceMapsUnavailable(f"{video_path} is not on disk")

        try:
            save(video_path, roi, compute_source_maps(video_path, roi))
        except (OSError, ValueError) as error:
            raise SourceMapsUnavailable(str(error)) from error

    return parse(path.read_bytes())


def map_png(maps: StoredMaps, source: int) -> bytes:
    ok, encoded = cv.imencode(".png", maps.weights[source])

    if not ok:
        raise SourceMapsUnavailable("Could not encode the source map")

    return encoded.tobytes()


def intensity(maps: StoredMaps, source: int) -> list[float]:
    """Return |signal| scaled to 0–1, the per-frame intensity of marajo's dynamic overlay."""
    return [round(int(value) / 255, 3) for value in maps.intensities[:, source]]
