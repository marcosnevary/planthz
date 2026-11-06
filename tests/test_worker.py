import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import HEIGHT, WIDTH

from planthz import jobs, store, worker
from planthz.db import connect
from planthz.rois import Roi
from planthz.store import RunStatus

PLANTS = {1: Roi(0, 0, WIDTH // 2, HEIGHT), 2: Roi(WIDTH // 2, 0, WIDTH // 2, HEIGHT)}


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = connect()

    yield connection

    connection.close()


def make_campaign(db: sqlite3.Connection, videos_dir: Path, *, with_rois: bool = True) -> int:
    campaign_id = store.create_campaign(db, "test", videos_dir)
    store.sync_videos(db, campaign_id)

    if with_rois:
        store.save_rois(db, campaign_id, PLANTS)

    return campaign_id


def run_job_in_process(db: sqlite3.Connection, campaign_id: int) -> int:
    runs = store.list_runs(db, campaign_id)
    total = sum(run.runnable for run in runs)
    cursor = db.execute(
        "INSERT INTO job (campaign_id, status, total, started_at, heartbeat_at) VALUES (?, 'running', ?, ?, ?)",
        (campaign_id, total, store.now(), store.now()),
    )
    db.commit()
    job_id = cursor.lastrowid
    assert job_id is not None
    worker.run_job(db, job_id)

    return job_id


def counts(db: sqlite3.Connection, campaign_id: int) -> dict[RunStatus, int]:
    return dict(store.status_counts(store.list_runs(db, campaign_id)))


def test_processes_every_pair_and_records_the_roi(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    job_id = run_job_in_process(db, campaign_id)

    job = db.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
    assert (job["status"], job["done"], job["failed"]) == ("completed", 6, 0)
    assert counts(db, campaign_id) == {RunStatus.DONE: 6}

    for run in store.list_runs(db, campaign_id):
        assert run.result is not None
        assert run.result.roi == PLANTS[run.plant_id]
        assert run.result.frequency is not None


def test_without_rois_nothing_is_runnable(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir, with_rois=False)

    assert counts(db, campaign_id) == {RunStatus.NO_ROI: 6}

    with pytest.raises(jobs.JobConflict):
        jobs.start_job(db, campaign_id)


def test_second_job_only_runs_what_is_left(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    run_job_in_process(db, campaign_id)
    run = store.list_runs(db, campaign_id)[0]
    db.execute("DELETE FROM result WHERE video_id = ? AND plant_id = ?", (run.video.id, run.plant_id))
    db.commit()

    job_id = run_job_in_process(db, campaign_id)

    assert db.execute("SELECT done FROM job WHERE id = ?", (job_id,)).fetchone()[0] == 1
    assert counts(db, campaign_id) == {RunStatus.DONE: 6}


def test_disabled_day_is_skipped_until_enabled_again(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    store.set_day_enabled(db, campaign_id, "2026-10-30", False)

    job_id = run_job_in_process(db, campaign_id)

    assert db.execute("SELECT done FROM job WHERE id = ?", (job_id,)).fetchone()[0] == 4
    assert counts(db, campaign_id) == {RunStatus.DONE: 4, RunStatus.PENDING: 2}

    store.set_day_enabled(db, campaign_id, "2026-10-30", True)
    run_job_in_process(db, campaign_id)

    assert counts(db, campaign_id) == {RunStatus.DONE: 6}


def test_disabling_every_day_leaves_nothing_to_start(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    store.set_day_enabled(db, campaign_id, "2026-10-29", False)
    store.set_day_enabled(db, campaign_id, "2026-10-30", False)

    with pytest.raises(jobs.JobConflict):
        jobs.start_job(db, campaign_id)


def test_disabling_a_day_keeps_its_results(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    run_job_in_process(db, campaign_id)

    store.set_day_enabled(db, campaign_id, "2026-10-30", False)
    store.sync_videos(db, campaign_id)

    assert counts(db, campaign_id) == {RunStatus.DONE: 6}
    assert store.disabled_days(db, campaign_id) == {"2026-10-30"}


def test_stop_request_is_honored_before_the_next_run(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    cursor = db.execute(
        "INSERT INTO job (campaign_id, status, total, stop_requested, started_at, heartbeat_at)"
        " VALUES (?, 'stopping', 6, 1, ?, ?)",
        (campaign_id, store.now(), store.now()),
    )
    db.commit()
    assert cursor.lastrowid is not None

    worker.run_job(db, cursor.lastrowid)

    assert db.execute("SELECT status FROM job").fetchone()[0] == "stopped"
    assert counts(db, campaign_id) == {RunStatus.PENDING: 6}


def test_broken_video_fails_alone_and_is_retried(db: sqlite3.Connection, videos_dir: Path, tmp_path: Path) -> None:
    root = tmp_path / "videos"
    shutil.copytree(videos_dir, root)
    broken = root / "2026-10-29" / "test_20261029_120000.mp4"
    good_bytes = broken.read_bytes()
    broken.write_bytes(b"not a video")
    campaign_id = make_campaign(db, root)

    job_id = run_job_in_process(db, campaign_id)

    job = db.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
    assert (job["status"], job["done"], job["failed"]) == ("completed", 6, 2)
    assert counts(db, campaign_id) == {RunStatus.FAILED: 2, RunStatus.DONE: 4}

    broken.write_bytes(good_bytes)
    run_job_in_process(db, campaign_id)

    assert counts(db, campaign_id) == {RunStatus.DONE: 6}


def test_changing_a_plants_roi_makes_only_its_results_stale(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    run_job_in_process(db, campaign_id)

    store.save_rois(db, campaign_id, {**PLANTS, 1: Roi(0, 0, WIDTH // 2, HEIGHT - 10)})

    assert counts(db, campaign_id) == {RunStatus.STALE: 3, RunStatus.DONE: 3}


def test_history_keeps_the_attempts_a_new_result_replaces(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    run_job_in_process(db, campaign_id)

    store.save_rois(db, campaign_id, {**PLANTS, 1: Roi(0, 0, WIDTH // 2, HEIGHT - 10)})
    run_job_in_process(db, campaign_id)

    history = store.run_history(db, campaign_id)
    assert db.execute("SELECT count(*) FROM result").fetchone()[0] == 6
    assert len(history) == 9
    assert [row["plant_id"] for row in history[:3]] == [1, 1, 1]
    assert [row["processed_at"] for row in history] == sorted((row["processed_at"] for row in history), reverse=True)


def test_deleting_a_campaign_deletes_its_history(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    run_job_in_process(db, campaign_id)

    store.delete_campaign(db, campaign_id)

    assert db.execute("SELECT count(*) FROM run_history").fetchone()[0] == 0


def test_video_that_left_the_disk_keeps_its_result(db: sqlite3.Connection, videos_dir: Path, tmp_path: Path) -> None:
    root = tmp_path / "videos"
    shutil.copytree(videos_dir, root)
    campaign_id = make_campaign(db, root)
    run_job_in_process(db, campaign_id)

    shutil.rmtree(root / "2026-10-30")
    store.sync_videos(db, campaign_id)

    assert counts(db, campaign_id) == {RunStatus.DONE: 6}
    assert sum(not video.on_disk for video in store.list_videos(db, campaign_id)) == 1


def test_missing_video_with_changed_roi_cannot_be_redone(
    db: sqlite3.Connection, videos_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "videos"
    shutil.copytree(videos_dir, root)
    campaign_id = make_campaign(db, root)
    run_job_in_process(db, campaign_id)

    shutil.rmtree(root / "2026-10-30")
    store.sync_videos(db, campaign_id)
    store.save_rois(db, campaign_id, {**PLANTS, 1: Roi(0, 0, WIDTH // 2, HEIGHT - 10)})

    status = counts(db, campaign_id)
    assert status[RunStatus.MISSING] == 1
    assert status[RunStatus.STALE] == 2


def test_dead_worker_closes_its_job_as_interrupted(db: sqlite3.Connection, videos_dir: Path) -> None:
    campaign_id = make_campaign(db, videos_dir)
    db.execute(
        "INSERT INTO job (campaign_id, status, pid, total, started_at, heartbeat_at) VALUES (?, 'running', ?, 6, ?, ?)",
        (campaign_id, 2**22 + 1, store.now(), store.now()),
    )
    db.commit()

    job = jobs.latest_job(db, campaign_id)

    assert job is not None
    assert job["status"] == "interrupted"
    assert jobs.active_job(db) is None
