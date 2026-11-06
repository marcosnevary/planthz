"""Finds the videos of a campaign in a folder that separates them by day."""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DAY_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CAPTURE_TIME = re.compile(r"(\d{8}_\d{6})")


@dataclass(frozen=True)
class FoundVideo:
    name: str
    path: Path
    day: str
    captured_at: datetime


@dataclass
class ScanResult:
    videos: list[FoundVideo] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


def scan_videos(root: Path) -> ScanResult:
    """List every .mp4 inside a YYYY-MM-DD folder at any depth, sorted by capture time."""
    result = ScanResult()
    names: set[str] = set()

    # followlinks because the dataset is often a symlink to wherever it was downloaded.
    for directory, _, files in os.walk(root, followlinks=True):
        folder = Path(directory)

        for file_name in sorted(files):
            # macOS leaves "._name.mp4" companions on external drives.
            if file_name.startswith(".") or not file_name.lower().endswith(".mp4"):
                continue

            path = folder / file_name
            relative = str(path.relative_to(root))

            if not DAY_FOLDER.match(folder.name):
                result.ignored.append(f"{relative}: fora de uma pasta AAAA-MM-DD")
                continue

            captured_at = parse_capture_time(file_name)

            if captured_at is None:
                result.ignored.append(f"{relative}: nome sem data e hora (AAAAMMDD_HHMMSS)")
                continue

            if file_name in names:
                result.ignored.append(f"{relative}: outro vídeo já tem esse nome")
                continue

            names.add(file_name)
            result.videos.append(FoundVideo(file_name, path, folder.name, captured_at))

    result.videos.sort(key=lambda video: video.captured_at)

    return result


def parse_capture_time(file_name: str) -> datetime | None:
    match = CAPTURE_TIME.search(file_name)

    if match is None:
        return None

    try:
        return datetime.strptime(match[1], "%Y%m%d_%H%M%S")
    except ValueError:
        return None
