"""The batch process: `python -m planthz.worker <job id>`."""

import sqlite3
import sys
import traceback

from planthz.db import connect
from planthz.jobs import JobStatus
from planthz.methodology import estimate_dominant_frequency
from planthz.store import list_runs, now, save_failure, save_result


def run_job(db: sqlite3.Connection, job_id: int) -> None:
    job = db.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
    runs = [run for run in list_runs(db, job["campaign_id"]) if run.runnable]
    failed = 0

    for done, run in enumerate(runs, start=1):
        if db.execute("SELECT stop_requested FROM job WHERE id = ?", (job_id,)).fetchone()[0]:
            _finish(db, job_id, JobStatus.STOPPED)
            return

        assert run.roi is not None

        try:
            estimate = estimate_dominant_frequency(run.video.path, run.roi)
            save_result(db, run.video.id, run.plant_id, run.roi, estimate)
        except Exception as error:
            # One unreadable video must not end a batch of hundreds.
            failed += 1
            save_failure(db, run.video.id, run.plant_id, run.roi, str(error) or type(error).__name__)

        db.execute(
            "UPDATE job SET done = ?, failed = ?, heartbeat_at = ? WHERE id = ?",
            (done, failed, now(), job_id),
        )
        # Result and progress commit together, so the counters never disagree with the results.
        db.commit()

    _finish(db, job_id, JobStatus.COMPLETED)


def _finish(db: sqlite3.Connection, job_id: int, status: JobStatus, error: str | None = None) -> None:
    db.execute(
        "UPDATE job SET status = ?, finished_at = ?, error = ? WHERE id = ?",
        (status, now(), error, job_id),
    )
    db.commit()


def main() -> None:
    job_id = int(sys.argv[1])
    db = connect()

    try:
        run_job(db, job_id)
    except Exception:
        traceback.print_exc()
        _finish(db, job_id, JobStatus.FAILED, traceback.format_exc(limit=1))
        raise


if __name__ == "__main__":
    main()
