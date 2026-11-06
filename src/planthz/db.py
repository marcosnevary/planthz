import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    videos_dir TEXT NOT NULL UNIQUE,
    ignored_files TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    day TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    on_disk INTEGER NOT NULL DEFAULT 1,
    UNIQUE (campaign_id, name)
);

CREATE INDEX IF NOT EXISTS video_campaign_time ON video (campaign_id, captured_at);

CREATE TABLE IF NOT EXISTS disabled_day (
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    PRIMARY KEY (campaign_id, day)
);

CREATE TABLE IF NOT EXISTS campaign_rois (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaign(id) ON DELETE CASCADE,
    plant_1_x INTEGER NOT NULL,
    plant_1_y INTEGER NOT NULL,
    plant_1_w INTEGER NOT NULL,
    plant_1_h INTEGER NOT NULL,
    plant_2_x INTEGER NOT NULL,
    plant_2_y INTEGER NOT NULL,
    plant_2_w INTEGER NOT NULL,
    plant_2_h INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS result (
    video_id INTEGER NOT NULL REFERENCES video(id) ON DELETE CASCADE,
    plant_id INTEGER NOT NULL,
    roi_x INTEGER NOT NULL,
    roi_y INTEGER NOT NULL,
    roi_w INTEGER NOT NULL,
    roi_h INTEGER NOT NULL,
    frequency REAL,
    amplitude REAL,
    source INTEGER,
    error TEXT,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (video_id, plant_id)
);

CREATE TABLE IF NOT EXISTS run_history (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES video(id) ON DELETE CASCADE,
    plant_id INTEGER NOT NULL,
    roi_x INTEGER NOT NULL,
    roi_y INTEGER NOT NULL,
    roi_w INTEGER NOT NULL,
    roi_h INTEGER NOT NULL,
    frequency REAL,
    amplitude REAL,
    source INTEGER,
    error TEXT,
    processed_at TEXT NOT NULL
);

-- A trigger so that no way of writing a result, now or later, can skip the history.
CREATE TRIGGER IF NOT EXISTS result_history AFTER INSERT ON result
BEGIN
    INSERT INTO run_history (
        video_id, plant_id, roi_x, roi_y, roi_w, roi_h, frequency, amplitude, source, error, processed_at
    ) VALUES (
        NEW.video_id, NEW.plant_id, NEW.roi_x, NEW.roi_y, NEW.roi_w, NEW.roi_h,
        NEW.frequency, NEW.amplitude, NEW.source, NEW.error, NEW.processed_at
    );
END;

CREATE TABLE IF NOT EXISTS job (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    pid INTEGER,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);
"""


_initialized: set[Path] = set()


def data_dir() -> Path:
    path = Path(os.environ.get("PLANTHZ_DATA_DIR", Path.home() / ".planthz"))
    path.mkdir(parents=True, exist_ok=True)

    return path


def _migrate_roi_segments(connection: sqlite3.Connection) -> None:
    """Replace the per-period ROI segments of older databases with one set of ROIs per campaign."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'roi_segment'").fetchone():
        return

    columns = ", ".join(f"plant_{plant_id}_{axis}" for plant_id in (1, 2) for axis in "xywh")

    with connection:
        # The latest segment is the one that applied to the most recent videos. Results computed with
        # any other segment no longer match, so they turn stale instead of being silently reused.
        connection.execute(
            f"""
            INSERT OR IGNORE INTO campaign_rois (campaign_id, {columns})
            SELECT campaign_id, {columns} FROM roi_segment AS segment
            WHERE starts_at = (SELECT max(starts_at) FROM roi_segment WHERE campaign_id = segment.campaign_id)
            """
        )
        connection.execute("DROP TABLE roi_segment")


def _backfill_run_history(connection: sqlite3.Connection) -> None:
    """Start the history of older databases from their results, which only hold each run's last attempt."""
    with connection:
        # One statement, because the web server and the worker can both open an older database at once.
        connection.execute(
            """
            INSERT INTO run_history (
                video_id, plant_id, roi_x, roi_y, roi_w, roi_h, frequency, amplitude, source, error, processed_at
            )
            SELECT video_id, plant_id, roi_x, roi_y, roi_w, roi_h, frequency, amplitude, source, error, processed_at
            FROM result
            WHERE NOT EXISTS (SELECT 1 FROM run_history)
            ORDER BY processed_at
            """
        )


def connect() -> sqlite3.Connection:
    path = data_dir() / "planthz.db"
    # check_same_thread off because FastAPI may run a request's dependency and route on different threads.
    connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    if path not in _initialized:
        # The web server reads while the worker process writes, which WAL allows without blocking.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        _migrate_roi_segments(connection)
        _backfill_run_history(connection)
        _initialized.add(path)

    return connection
