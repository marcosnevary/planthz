"""Writes a campaign to a ZIP file and recreates it from that file, for sharing between machines.

The ZIP holds the campaign as one JSON file, plus the source maps and video frames the video page
needs to show the maps without the videos. Exports from before the maps are that JSON file alone.
"""

import json
import sqlite3
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import IO

import cv2 as cv
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, NaiveDatetime, ValidationError

from planthz import frames, source_maps, store
from planthz.rois import PLANT_IDS, Roi, validate_plants

FORMAT = "planthz-campaign"
VERSION = 1

# Far above any real export. Picking a video by mistake must not load gigabytes into memory.
MAX_SIZE = 100 * 1024 * 1024

ARCHIVE_CAMPAIGN = "campaign.json"
# Far above one run's maps or one full-resolution frame, so a damaged archive cannot fill the memory.
MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024

NOT_AN_EXPORT = "Esse arquivo não é uma exportação de campanha do planthz."
NEWER_VERSION = "Esse arquivo foi exportado por uma versão mais nova do planthz. Atualize o planthz para importá-lo."
DAMAGED = "Esse arquivo está incompleto ou corrompido. Exporte a campanha novamente."


class ImportRejected(Exception):
    """Raised with a message for the interface when a file cannot be imported."""


class _Model(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)


class ExportedCampaign(_Model):
    name: str
    videos_dir: str
    ignored_files: list[str]
    created_at: NaiveDatetime


class ExportedRois(_Model):
    plant_1: Roi
    plant_2: Roi


class ExportedResult(_Model):
    plant_id: int = Field(ge=min(PLANT_IDS), le=max(PLANT_IDS))
    roi: Roi
    frequency: float | None
    amplitude: float | None
    source: int | None
    error: str | None
    processed_at: NaiveDatetime


class ExportedVideo(_Model):
    name: str
    path: str
    day: date
    captured_at: NaiveDatetime
    results: list[ExportedResult]


class CampaignExport(_Model):
    format: str = FORMAT
    version: int = VERSION
    campaign: ExportedCampaign
    rois: ExportedRois | None
    disabled_days: list[date]
    videos: list[ExportedVideo]


def campaign_json(db: sqlite3.Connection, campaign_id: int) -> str:
    campaign = store.get_campaign(db, campaign_id)
    assert campaign is not None
    rois = store.get_rois(db, campaign_id)
    results: dict[int, list[ExportedResult]] = defaultdict(list)

    for run in store.list_runs(db, campaign_id):
        if run.result is not None:
            results[run.video.id].append(
                ExportedResult(
                    plant_id=run.plant_id,
                    roi=run.result.roi,
                    frequency=run.result.frequency,
                    amplitude=run.result.amplitude,
                    source=run.result.source,
                    error=run.result.error,
                    processed_at=run.result.processed_at,
                )
            )

    export = CampaignExport(
        campaign=ExportedCampaign(
            name=campaign["name"],
            videos_dir=campaign["videos_dir"],
            ignored_files=store.ignored_files(campaign),
            created_at=datetime.fromisoformat(campaign["created_at"]),
        ),
        rois=ExportedRois(plant_1=rois[1], plant_2=rois[2]) if rois else None,
        disabled_days=sorted(date.fromisoformat(day) for day in store.disabled_days(db, campaign_id)),
        videos=[
            ExportedVideo(
                name=video.name,
                path=str(video.path),
                day=date.fromisoformat(video.day),
                captured_at=video.captured_at,
                results=results[video.id],
            )
            for video in store.list_videos(db, campaign_id)
        ],
    )

    return export.model_dump_json(indent=1)


def _parse(content: bytes) -> CampaignExport:
    if len(content) > MAX_SIZE:
        raise ImportRejected(NOT_AN_EXPORT)

    try:
        data = json.loads(content)
    except (ValueError, RecursionError) as error:
        raise ImportRejected(NOT_AN_EXPORT) from error

    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ImportRejected(NOT_AN_EXPORT)

    version = data.get("version")

    if isinstance(version, int) and version > VERSION:
        raise ImportRejected(NEWER_VERSION)

    if version != VERSION:
        raise ImportRejected(DAMAGED)

    try:
        export = CampaignExport.model_validate_json(content)
    except ValidationError as error:
        raise ImportRejected(DAMAGED) from error

    if export.rois and validate_plants({1: export.rois.plant_1, 2: export.rois.plant_2}) is not None:
        raise ImportRejected(DAMAGED)

    return export


@dataclass(frozen=True)
class _Attachment:
    """A video's frame (without a ROI) or a run's source maps, as named in the archive."""

    name: str
    video_path: Path
    roi: Roi | None = None

    @property
    def cache_path(self) -> Path:
        if self.roi is None:
            return frames.cache_path(self.video_path, 0)

        return source_maps.cache_path(self.video_path, self.roi)

    def check(self, content: bytes) -> None:
        if self.roi is None:
            valid = cv.imdecode(np.frombuffer(content, np.uint8), cv.IMREAD_COLOR) is not None
        else:
            try:
                source_maps.parse(content)
                valid = True
            except source_maps.SourceMapsUnavailable:
                valid = False

        if not valid:
            raise ImportRejected(DAMAGED)

    def save(self, content: bytes) -> None:
        if self.roi is None:
            frames.store_frame(self.video_path, 0, content)
        else:
            source_maps.write(self.video_path, self.roi, content)


def _attachments(videos: list[tuple[str, Path, list[tuple[int, Roi]]]]) -> Iterator[_Attachment]:
    """Yield the attachments a list of (video name, path, [(plant, ROI) of each result]) can have."""
    for name, path, results in videos:
        yield _Attachment(f"frames/{name}.jpg", path)

        for plant_id, roi in results:
            yield _Attachment(f"source_maps/{name}/plant_{plant_id}.npz", path, roi)


def export_campaign(db: sqlite3.Connection, campaign_id: int, file: IO[bytes]) -> None:
    """Write the export with the source maps and frames this machine has for the campaign's results."""
    results: dict[int, list[tuple[int, Roi]]] = defaultdict(list)

    for run in store.list_runs(db, campaign_id):
        if run.result is not None and run.result.error is None:
            results[run.video.id].append((run.plant_id, run.result.roi))

    videos = [(video.name, video.path, results[video.id]) for video in store.list_videos(db, campaign_id)]

    # The maps and frames are already compressed, so compressing them again only costs time.
    with zipfile.ZipFile(file, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(ARCHIVE_CAMPAIGN, campaign_json(db, campaign_id), zipfile.ZIP_DEFLATED)

        for attachment in _attachments(videos):
            if attachment.cache_path.is_file():
                archive.write(attachment.cache_path, attachment.name)


def import_file(db: sqlite3.Connection, file: IO[bytes]) -> int:
    """Create a campaign from an export or an archive, told apart by their content."""
    is_archive = zipfile.is_zipfile(file)
    file.seek(0)

    if is_archive:
        return _import_archive(db, file)

    return import_campaign(db, file.read(MAX_SIZE + 1))


def _import_archive(db: sqlite3.Connection, file: IO[bytes]) -> int:
    try:
        with zipfile.ZipFile(file) as archive:
            if ARCHIVE_CAMPAIGN not in archive.namelist():
                raise ImportRejected(NOT_AN_EXPORT)

            export = _parse(_read_member(archive, ARCHIVE_CAMPAIGN, MAX_SIZE))
            names = set(archive.namelist())
            videos = [
                (video.name, Path(video.path), [(r.plant_id, r.roi) for r in video.results if r.error is None])
                for video in export.videos
            ]
            attachments = [attachment for attachment in _attachments(videos) if attachment.name in names]

            # Checked before anything is saved, so a damaged attachment leaves the database unchanged.
            for attachment in attachments:
                attachment.check(_read_member(archive, attachment.name, MAX_ATTACHMENT_SIZE))

            campaign_id = _create_campaign(db, export)

            for attachment in attachments:
                attachment.save(_read_member(archive, attachment.name, MAX_ATTACHMENT_SIZE))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError) as error:
        raise ImportRejected(DAMAGED) from error

    return campaign_id


def _read_member(archive: zipfile.ZipFile, name: str, max_size: int) -> bytes:
    # The size in the directory can lie, so the read is limited too.
    if archive.getinfo(name).file_size > max_size:
        raise ImportRejected(DAMAGED)

    with archive.open(name) as member:
        content = member.read(max_size + 1)

    if len(content) > max_size:
        raise ImportRejected(DAMAGED)

    return content


def import_campaign(db: sqlite3.Connection, content: bytes) -> int:
    """Create a campaign from an export. Either all of it is saved or nothing is."""
    return _create_campaign(db, _parse(content))


def _create_campaign(db: sqlite3.Connection, export: CampaignExport) -> int:
    campaign = export.campaign
    existing = db.execute("SELECT name FROM campaign WHERE videos_dir = ?", (campaign.videos_dir,)).fetchone()

    if existing:
        raise ImportRejected(
            f"A campanha “{existing['name']}” já usa a pasta {campaign.videos_dir}. "
            "Exclua-a antes de importar este arquivo."
        )

    try:
        with db:
            cursor = db.execute(
                "INSERT INTO campaign (name, videos_dir, ignored_files, created_at) VALUES (?, ?, ?, ?)",
                (
                    campaign.name,
                    campaign.videos_dir,
                    json.dumps(campaign.ignored_files, ensure_ascii=False),
                    campaign.created_at.isoformat(),
                ),
            )
            campaign_id = cursor.lastrowid
            assert campaign_id is not None

            if export.rois:
                store.insert_rois(db, campaign_id, {1: export.rois.plant_1, 2: export.rois.plant_2})

            db.executemany(
                "INSERT INTO disabled_day (campaign_id, day) VALUES (?, ?)",
                [(campaign_id, day.isoformat()) for day in export.disabled_days],
            )

            for video in export.videos:
                _insert_video(db, campaign_id, video)
    except (sqlite3.IntegrityError, OverflowError) as error:
        # Repeated videos, days or runs, or integers that do not fit in a SQLite column.
        raise ImportRejected(DAMAGED) from error

    return campaign_id


def _insert_video(db: sqlite3.Connection, campaign_id: int, video: ExportedVideo) -> None:
    cursor = db.execute(
        "INSERT INTO video (campaign_id, name, path, day, captured_at, on_disk) VALUES (?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            video.name,
            video.path,
            video.day.isoformat(),
            video.captured_at.isoformat(),
            # Says whether the file is on this machine, so it comes from here and not from the export.
            Path(video.path).is_file(),
        ),
    )
    db.executemany(
        """
        INSERT INTO result (
            video_id, plant_id, roi_x, roi_y, roi_w, roi_h, frequency, amplitude, source, error, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                cursor.lastrowid,
                result.plant_id,
                result.roi.x,
                result.roi.y,
                result.roi.w,
                result.roi.h,
                result.frequency,
                result.amplitude,
                result.source,
                result.error,
                result.processed_at.isoformat(),
            )
            for result in video.results
        ],
    )
