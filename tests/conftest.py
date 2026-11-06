from collections.abc import Iterator
from pathlib import Path

import cv2 as cv
import numpy as np
import pytest

FPS = 30.0
WIDTH, HEIGHT = 200, 120
PLANT_FREQUENCIES = {1: 3.0, 2: 5.0}


def write_video(path: Path, n_frames: int = 150) -> None:
    """Each half of the frame oscillates in brightness at the frequency of its plant."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv.VideoWriter(str(path), cv.VideoWriter.fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    random = np.random.default_rng(0)

    for index in range(n_frames):
        time = index / FPS
        frame = random.normal(120, 2, (HEIGHT, WIDTH, 3))
        frame[:, : WIDTH // 2] += 60 * np.sin(2 * np.pi * PLANT_FREQUENCIES[1] * time)
        frame[:, WIDTH // 2 :] += 60 * np.sin(2 * np.pi * PLANT_FREQUENCIES[2] * time)
        writer.write(np.clip(frame, 0, 255).astype(np.uint8))

    writer.release()


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "data"
    monkeypatch.setenv("PLANTHZ_DATA_DIR", str(path))

    yield path


@pytest.fixture(scope="session")
def videos_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("campaign")

    for relative in [
        "2026-10-29/test_20261029_120000.mp4",
        "2026-10-29/test_20261029_123000.mp4",
        "2026-10-30/test_20261030_080000.mp4",
    ]:
        write_video(root / relative)

    return root
