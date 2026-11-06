"""Starts, stops and inspects the batch job, which runs in its own process (see worker.py)."""

import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from enum import StrEnum

from planthz.db import data_dir
from planthz.store import list_runs, now, sync_videos

# Far above the seconds a run takes, so only a dead or stuck worker gets here.
HEARTBEAT_LIMIT = timedelta(minutes=5)


class JobStatus(StrEnum):
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


ACTIVE = {JobStatus.RUNNING, JobStatus.STOPPING}


class JobConflict(Exception):
    """Raised with a message for the interface when a job cannot start."""


def _worker_is_alive(job: sqlite3.Row) -> bool:
    if datetime.now() - datetime.fromisoformat(job["heartbeat_at"]) > HEARTBEAT_LIMIT:
        return False

    try:
        os.kill(job["pid"], 0)
    except (OSError, TypeError):
        return False

    return True


def _close_dead_jobs(db: sqlite3.Connection) -> None:
    """A worker killed mid-batch cannot report it, so its job is closed by whoever looks next."""
    for job in db.execute("SELECT * FROM job WHERE status IN ('running', 'stopping')").fetchall():
        if not _worker_is_alive(job):
            db.execute(
                "UPDATE job SET status = ?, finished_at = ? WHERE id = ?",
                (JobStatus.INTERRUPTED, now(), job["id"]),
            )

    db.commit()


def latest_job(db: sqlite3.Connection, campaign_id: int) -> sqlite3.Row | None:
    _close_dead_jobs(db)

    return db.execute("SELECT * FROM job WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (campaign_id,)).fetchone()


def active_job(db: sqlite3.Connection) -> sqlite3.Row | None:
    _close_dead_jobs(db)

    return db.execute("SELECT * FROM job WHERE status IN ('running', 'stopping') LIMIT 1").fetchone()


def start_job(db: sqlite3.Connection, campaign_id: int) -> int:
    # One job at a time across campaigns: two would compete for the CPU and the disk.
    if active_job(db) is not None:
        raise JobConflict("Já existe um processamento em andamento.")

    sync_videos(db, campaign_id)
    total = sum(run.runnable for run in list_runs(db, campaign_id))

    if total == 0:
        raise JobConflict("Não há execuções a processar.")

    timestamp = now()
    cursor = db.execute(
        "INSERT INTO job (campaign_id, status, total, started_at, heartbeat_at) VALUES (?, ?, ?, ?, ?)",
        (campaign_id, JobStatus.RUNNING, total, timestamp, timestamp),
    )
    job_id = cursor.lastrowid
    assert job_id is not None
    db.commit()

    log_dir = data_dir() / "logs"
    log_dir.mkdir(exist_ok=True)

    with (log_dir / f"job_{job_id}.log").open("ab") as log:
        # A new session keeps the batch alive when the web server stops or reloads.
        process = subprocess.Popen(
            [sys.executable, "-m", "planthz.worker", str(job_id)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    # Reaps the worker when it exits. Otherwise it lingers as a zombie that still looks alive to os.kill.
    threading.Thread(target=process.wait, daemon=True).start()

    db.execute("UPDATE job SET pid = ? WHERE id = ?", (process.pid, job_id))
    db.commit()

    return job_id


def request_stop(db: sqlite3.Connection, campaign_id: int) -> None:
    """The worker stops after the current run, so everything already computed is kept."""
    db.execute(
        "UPDATE job SET stop_requested = 1, status = ? WHERE campaign_id = ? AND status = ?",
        (JobStatus.STOPPING, campaign_id, JobStatus.RUNNING),
    )
    db.commit()


def seconds_left(job: sqlite3.Row) -> float | None:
    if job["done"] == 0:
        return None

    elapsed = (datetime.fromisoformat(job["heartbeat_at"]) - datetime.fromisoformat(job["started_at"])).total_seconds()

    return elapsed / job["done"] * (job["total"] - job["done"])
