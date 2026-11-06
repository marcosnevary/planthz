import sqlite3

from planthz import store
from planthz.db import SCHEMA, connect, data_dir
from planthz.rois import Roi

ROI_SEGMENT_TABLE = """
CREATE TABLE roi_segment (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    starts_at TEXT NOT NULL,
    plant_1_x INTEGER NOT NULL,
    plant_1_y INTEGER NOT NULL,
    plant_1_w INTEGER NOT NULL,
    plant_1_h INTEGER NOT NULL,
    plant_2_x INTEGER NOT NULL,
    plant_2_y INTEGER NOT NULL,
    plant_2_w INTEGER NOT NULL,
    plant_2_h INTEGER NOT NULL
);
"""


def test_older_database_keeps_the_latest_roi_segment_of_each_campaign() -> None:
    old = sqlite3.connect(data_dir() / "planthz.db")
    old.executescript(SCHEMA + ROI_SEGMENT_TABLE)
    old.execute("INSERT INTO campaign (id, name, videos_dir, created_at) VALUES (1, 'a', '/a', 'x')")
    old.executemany(
        "INSERT INTO roi_segment VALUES (NULL, 1, ?, ?, 0, 10, 10, 20, 0, 10, 10)",
        [("2026-10-29T00:00:00", 1), ("2026-10-31T00:00:00", 3), ("2026-10-30T00:00:00", 2)],
    )
    old.commit()
    old.close()

    db = connect()

    assert store.get_rois(db, 1) == {1: Roi(3, 0, 10, 10), 2: Roi(20, 0, 10, 10)}
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name = 'roi_segment'").fetchone() is None
    db.close()
