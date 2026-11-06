"""Reads and writes campaigns, videos, ROIs and results, and decides the status of each run."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from planthz.methodology import Estimate
from planthz.rois import PLANT_IDS, Roi
from planthz.scan import scan_videos


class RunStatus(StrEnum):
    """State of one (video, plant) pair."""

    DONE = "done"
    NO_PEAK = "no_peak"
    FAILED = "failed"
    STALE = "stale"
    PENDING = "pending"
    NO_ROI = "no_roi"
    MISSING = "missing"


# Runs a job would (re)compute. Failed runs are retried, as in the original batch.
RUNNABLE = {RunStatus.FAILED, RunStatus.STALE, RunStatus.PENDING}


@dataclass(frozen=True)
class Video:
    id: int
    campaign_id: int
    name: str
    path: Path
    day: str
    captured_at: datetime
    on_disk: bool


@dataclass(frozen=True)
class Result:
    roi: Roi
    frequency: float | None
    amplitude: float | None
    source: int | None
    error: str | None
    processed_at: datetime


@dataclass(frozen=True)
class Run:
    video: Video
    plant_id: int
    roi: Roi | None
    result: Result | None
    status: RunStatus
    day_enabled: bool = True

    @property
    def runnable(self) -> bool:
        return self.day_enabled and self.status in RUNNABLE


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- Campaigns ---------------------------------------------------------------------------------


def list_campaigns(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM campaign ORDER BY created_at DESC").fetchall()


def get_campaign(db: sqlite3.Connection, campaign_id: int) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM campaign WHERE id = ?", (campaign_id,)).fetchone()


def create_campaign(db: sqlite3.Connection, name: str, videos_dir: Path) -> int:
    cursor = db.execute(
        "INSERT INTO campaign (name, videos_dir, created_at) VALUES (?, ?, ?)",
        (name, str(videos_dir), now()),
    )
    db.commit()
    assert cursor.lastrowid is not None

    return cursor.lastrowid


def delete_campaign(db: sqlite3.Connection, campaign_id: int) -> None:
    db.execute("DELETE FROM campaign WHERE id = ?", (campaign_id,))
    db.commit()


def ignored_files(campaign: sqlite3.Row) -> list[str]:
    return json.loads(campaign["ignored_files"])


def sync_videos(db: sqlite3.Connection, campaign_id: int) -> None:
    """Bring the video list in line with the folder.

    Videos that left the disk stay, marked as missing: an analysis only needs their results, and
    the dataset is too large to keep every week on disk.
    """
    campaign = get_campaign(db, campaign_id)
    assert campaign is not None
    scan = scan_videos(Path(campaign["videos_dir"]))

    db.execute("UPDATE video SET on_disk = 0 WHERE campaign_id = ?", (campaign_id,))
    db.executemany(
        """
        INSERT INTO video (campaign_id, name, path, day, captured_at, on_disk) VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT (campaign_id, name) DO UPDATE SET path = excluded.path, day = excluded.day, on_disk = 1
        """,
        [(campaign_id, v.name, str(v.path), v.day, v.captured_at.isoformat()) for v in scan.videos],
    )
    db.execute(
        "UPDATE campaign SET ignored_files = ? WHERE id = ?",
        (json.dumps(scan.ignored, ensure_ascii=False), campaign_id),
    )
    db.commit()


# --- Videos ------------------------------------------------------------------------------------


def _to_video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        campaign_id=row["campaign_id"],
        name=row["name"],
        path=Path(row["path"]),
        day=row["day"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
        on_disk=bool(row["on_disk"]),
    )


def list_videos(db: sqlite3.Connection, campaign_id: int, day: str | None = None) -> list[Video]:
    query = "SELECT * FROM video WHERE campaign_id = ?"
    params: list[object] = [campaign_id]

    if day is not None:
        query += " AND day = ?"
        params.append(day)

    return [_to_video(row) for row in db.execute(query + " ORDER BY captured_at", params)]


def get_video(db: sqlite3.Connection, video_id: int) -> Video | None:
    row = db.execute("SELECT * FROM video WHERE id = ?", (video_id,)).fetchone()

    return _to_video(row) if row else None


def neighbor_videos(db: sqlite3.Connection, video: Video) -> tuple[int | None, int | None]:
    """Return the ids of the videos captured right before and after this one."""
    params = (video.campaign_id, video.captured_at.isoformat())
    previous = db.execute(
        "SELECT id FROM video WHERE campaign_id = ? AND captured_at < ? ORDER BY captured_at DESC LIMIT 1", params
    ).fetchone()
    following = db.execute(
        "SELECT id FROM video WHERE campaign_id = ? AND captured_at > ? ORDER BY captured_at LIMIT 1", params
    ).fetchone()

    return (previous["id"] if previous else None, following["id"] if following else None)


# --- Days --------------------------------------------------------------------------------------


def disabled_days(db: sqlite3.Connection, campaign_id: int) -> set[str]:
    rows = db.execute("SELECT day FROM disabled_day WHERE campaign_id = ?", (campaign_id,))

    return {row["day"] for row in rows}


def set_day_enabled(db: sqlite3.Connection, campaign_id: int, day: str, enabled: bool) -> None:
    if enabled:
        db.execute("DELETE FROM disabled_day WHERE campaign_id = ? AND day = ?", (campaign_id, day))
    else:
        db.execute("INSERT OR IGNORE INTO disabled_day (campaign_id, day) VALUES (?, ?)", (campaign_id, day))

    db.commit()


# --- ROIs --------------------------------------------------------------------------------------


def get_rois(db: sqlite3.Connection, campaign_id: int) -> dict[int, Roi] | None:
    row = db.execute("SELECT * FROM campaign_rois WHERE campaign_id = ?", (campaign_id,)).fetchone()

    if row is None:
        return None

    return {
        plant_id: Roi(
            row[f"plant_{plant_id}_x"],
            row[f"plant_{plant_id}_y"],
            row[f"plant_{plant_id}_w"],
            row[f"plant_{plant_id}_h"],
        )
        for plant_id in PLANT_IDS
    }


def save_rois(db: sqlite3.Connection, campaign_id: int, plants: dict[int, Roi]) -> None:
    insert_rois(db, campaign_id, plants)
    db.commit()


def insert_rois(db: sqlite3.Connection, campaign_id: int, plants: dict[int, Roi]) -> None:
    p1, p2 = plants[1], plants[2]
    db.execute(
        """
        INSERT OR REPLACE INTO campaign_rois (
            campaign_id,
            plant_1_x, plant_1_y, plant_1_w, plant_1_h,
            plant_2_x, plant_2_y, plant_2_w, plant_2_h
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (campaign_id, p1.x, p1.y, p1.w, p1.h, p2.x, p2.y, p2.w, p2.h),
    )


# --- Runs --------------------------------------------------------------------------------------


def _to_result(row: sqlite3.Row) -> Result:
    return Result(
        roi=Roi(row["roi_x"], row["roi_y"], row["roi_w"], row["roi_h"]),
        frequency=row["frequency"],
        amplitude=row["amplitude"],
        source=row["source"],
        error=row["error"],
        processed_at=datetime.fromisoformat(row["processed_at"]),
    )


def run_status(video: Video, roi: Roi | None, result: Result | None) -> RunStatus:
    # A result is only trusted while it was computed with the ROI that applies to the video today.
    if result is not None and result.error is None and result.roi == roi:
        return RunStatus.DONE if result.frequency is not None else RunStatus.NO_PEAK

    if not video.on_disk:
        return RunStatus.MISSING

    if roi is None:
        return RunStatus.NO_ROI

    if result is None:
        return RunStatus.PENDING

    return RunStatus.FAILED if result.error is not None else RunStatus.STALE


def list_runs(db: sqlite3.Connection, campaign_id: int, day: str | None = None) -> list[Run]:
    rois = get_rois(db, campaign_id)
    videos = list_videos(db, campaign_id, day)
    rows = db.execute(
        """
        SELECT result.* FROM result JOIN video ON video.id = result.video_id
        WHERE video.campaign_id = ?
        """,
        (campaign_id,),
    )
    results = {(row["video_id"], row["plant_id"]): _to_result(row) for row in rows}
    disabled = disabled_days(db, campaign_id)

    runs = []

    for video in videos:
        for plant_id in PLANT_IDS:
            roi = rois[plant_id] if rois else None
            result = results.get((video.id, plant_id))
            runs.append(Run(video, plant_id, roi, result, run_status(video, roi, result), video.day not in disabled))

    return runs


def status_counts(runs: list[Run]) -> Counter[RunStatus]:
    return Counter(run.status for run in runs)


def save_result(db: sqlite3.Connection, video_id: int, plant_id: int, roi: Roi, estimate: Estimate) -> None:
    _upsert_result(db, video_id, plant_id, roi, estimate.frequency, estimate.amplitude, estimate.source, None)


def save_failure(db: sqlite3.Connection, video_id: int, plant_id: int, roi: Roi, error: str) -> None:
    _upsert_result(db, video_id, plant_id, roi, None, None, None, error)


def _upsert_result(
    db: sqlite3.Connection,
    video_id: int,
    plant_id: int,
    roi: Roi,
    frequency: float | None,
    amplitude: float | None,
    source: int | None,
    error: str | None,
) -> None:
    db.execute(
        """
        INSERT OR REPLACE INTO result (
            video_id, plant_id, roi_x, roi_y, roi_w, roi_h, frequency, amplitude, source, error, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (video_id, plant_id, roi.x, roi.y, roi.w, roi.h, frequency, amplitude, source, error, now()),
    )


def latest_runs(db: sqlite3.Connection, campaign_id: int, limit: int = 8) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT video.id AS video_id, video.name, result.plant_id, result.frequency, result.processed_at
        FROM result JOIN video ON video.id = result.video_id
        WHERE video.campaign_id = ?
        ORDER BY result.processed_at DESC, video.captured_at DESC, result.plant_id DESC
        LIMIT ?
        """,
        (campaign_id, limit),
    ).fetchall()
