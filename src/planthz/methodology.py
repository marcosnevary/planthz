"""Estimates the dominant frequency of a plant from a video and its ROI.

See docs/methodology.md. Every numeric step must stay equivalent to the marajo
notebooks, otherwise results stop being comparable with the published ones.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2 as cv
import numpy as np
from scipy.linalg import eig
from scipy.signal import find_peaks, lfilter
from sklearn.decomposition import PCA

from planthz.rois import Roi

N_PC = 10
NFRAMES_OUT = 630
SCALE_OUT = 0.2
FREQUENCY_THRESHOLD = 1.0


@dataclass(frozen=True)
class Estimate:
    """Dominant source of a run. A run without any peak above the threshold has no frequency."""

    frequency: float | None
    amplitude: float | None
    source: int | None


@dataclass(frozen=True)
class SourceMaps:
    """Where in the ROI each separated source lives and how it varies over time.

    `maps` has shape (sources, height, width) at the preprocessed scale, and `signals` has shape
    (frames, sources). Both have an arbitrary sign and scale per source.
    """

    maps: np.ndarray
    signals: np.ndarray
    peaks: list[tuple[float, float]]
    fps: float

    @property
    def dominant(self) -> Estimate:
        return dominant_source(self.peaks)


def estimate_dominant_frequency(video_path: Path, roi: Roi) -> Estimate:
    frames, fps = read_preprocessed(video_path, roi)
    components, _ = principal_components(_flatten(frames))

    return dominant_source(source_peaks(separate_sources(components[:, :N_PC]), fps))


def compute_source_maps(video_path: Path, roi: Roi) -> SourceMaps:
    """Weight of each source on each pixel, as marajo's compute_source_spatial_maps."""
    frames, fps = read_preprocessed(video_path, roi)
    components, scores = principal_components(_flatten(frames))
    mixtures = components[:, :N_PC]
    sources = separate_sources(mixtures)

    mixing, _, _, _ = np.linalg.lstsq(sources, mixtures, rcond=None)
    maps = (scores[:, :N_PC] @ mixing.T).T.reshape(N_PC, *frames.shape[1:])

    return SourceMaps(maps=maps, signals=sources, peaks=source_peaks(sources, fps), fps=fps)


def read_preprocessed(video_path: Path, roi: Roi) -> tuple[np.ndarray, float]:
    """Return the preprocessed frames, with shape (frames, height, width), and their fps."""
    with tempfile.TemporaryDirectory(prefix="planthz-") as temp_dir:
        preprocessed_path = Path(temp_dir) / "preprocessed.mp4"
        preprocess(video_path, preprocessed_path, roi)

        # The re-encoded container reports a rounded fps (59.94, not 59.94006), and the frequency
        # axis has always been built from it.
        fps = read_fps(preprocessed_path)
        frames = load_grayscale_frames(preprocessed_path)

    return frames, fps


def _flatten(frames: np.ndarray) -> np.ndarray:
    return frames.reshape(len(frames), -1)


def source_peaks(sources: np.ndarray, fps: float) -> list[tuple[float, float]]:
    """Return the (frequency, power) of the highest peak of each separated source."""
    return [highest_peak(sources[:, i], fps) for i in range(N_PC)]


def preprocess(video_path: Path, output_path: Path, roi: Roi) -> None:
    """Crop to the ROI, convert to grayscale, downscale and re-encode the video."""
    video = cv.VideoCapture(str(video_path))

    if not video.isOpened():
        raise OSError(f"Could not open video: {video_path}")

    writer = None

    try:
        frame_width = int(video.get(cv.CAP_PROP_FRAME_WIDTH))
        frame_height = int(video.get(cv.CAP_PROP_FRAME_HEIGHT))

        # NumPy slicing would silently clip the crop and stretch it back to the expected size.
        if not roi.fits_in(frame_width, frame_height):
            raise ValueError(f"ROI {roi} does not fit in the {frame_width}x{frame_height} frame.")

        width = int(roi.w * SCALE_OUT)
        height = int(roi.h * SCALE_OUT)

        if width <= 0 or height <= 0:
            raise ValueError(f"ROI {roi} is too small.")

        writer = cv.VideoWriter(
            str(output_path),
            cv.VideoWriter.fourcc(*"mp4v"),
            video.get(cv.CAP_PROP_FPS),
            (width, height),
            isColor=False,
        )

        if not writer.isOpened():
            raise OSError(f"Could not create video: {output_path}")

        for _ in range(NFRAMES_OUT):
            ok, frame = video.read()

            if not ok:
                break

            cropped = frame[roi.y : roi.y + roi.h, roi.x : roi.x + roi.w]
            gray = cv.cvtColor(cropped, cv.COLOR_BGR2GRAY)
            writer.write(cv.resize(gray, (width, height), interpolation=cv.INTER_AREA))

    finally:
        video.release()

        if writer is not None:
            writer.release()


def read_fps(video_path: Path) -> float:
    video = cv.VideoCapture(str(video_path))

    try:
        return float(video.get(cv.CAP_PROP_FPS))
    finally:
        video.release()


def load_grayscale_frames(video_path: Path) -> np.ndarray:
    """Return the frames with shape (frames, height, width), read back at the size the encoder wrote."""
    video = cv.VideoCapture(str(video_path))

    if not video.isOpened():
        raise OSError(f"Could not open video: {video_path}")

    frames = []

    try:
        while True:
            ok, frame = video.read()

            if not ok:
                break

            frames.append(frame[:, :, 0])

    finally:
        video.release()

    if not frames:
        raise ValueError("The preprocessed video has no frames.")

    return np.asarray(frames, dtype=np.float32)


def principal_components(dataset: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the PCA components over time, with shape (frames, components), and the scores of each
    pixel on them, with shape (pixels, components)."""
    centered = dataset - dataset.mean(axis=0, keepdims=True)
    pca = PCA()
    scores = pca.fit_transform(centered.T)

    return pca.components_.T, scores


def separate_sources(mixtures: np.ndarray) -> np.ndarray:
    """Blind source separation by temporal predictability (Stone's CP algorithm)."""
    short_term = lfilter(_predictability_mask(half_life=1), 1, mixtures, axis=0)
    long_term = lfilter(_predictability_mask(half_life=900000), 1, mixtures, axis=0)

    short_covariance = np.cov(short_term, rowvar=False, bias=True)
    long_covariance = np.cov(long_term, rowvar=False, bias=True)

    _, unmixing = eig(long_covariance, short_covariance)
    sources = -(mixtures @ np.real(unmixing))

    return -np.fliplr(sources)


def _predictability_mask(half_life: int, half_lives_in_mask: int = 10, max_length: int = 50) -> np.ndarray:
    length = max(min(half_lives_in_mask * half_life, max_length), 1)

    mask = (2 ** (-1 / half_life)) ** np.arange(length)
    mask[0] = 0
    mask = mask / np.sum(np.abs(mask))
    mask[0] = -1

    return mask


def highest_peak(signal: np.ndarray, fps: float) -> tuple[float, float]:
    """Return the (frequency, power) of the highest peak of the signal's spectrum."""
    frequencies = np.fft.rfftfreq(len(signal), d=1 / fps)
    power = np.abs(np.fft.rfft(signal)) ** 2

    positive = frequencies > 0
    frequencies = frequencies[positive]
    power = power[positive]

    peaks, _ = find_peaks(power)

    if len(peaks) == 0:
        peaks = np.array([np.argmax(power)])

    highest = peaks[np.argsort(power[peaks])[-1]]

    return float(frequencies[highest]), float(power[highest])


def dominant_source(peaks: list[tuple[float, float]]) -> Estimate:
    """Pick the source with the highest power among those whose peak is above the threshold."""
    best = Estimate(frequency=None, amplitude=None, source=None)

    for source, (frequency, amplitude) in enumerate(peaks):
        if frequency > FREQUENCY_THRESHOLD and amplitude > (best.amplitude or 0):
            best = Estimate(frequency=frequency, amplitude=amplitude, source=source)

    return best
