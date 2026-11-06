# Architecture

## Processes

- **Web server** (`web.py`): FastAPI with Jinja2 templates and HTMX. It binds to localhost only, because it reads any folder the user types.
- **Worker** (`worker.py`): one process per job, started by `jobs.py` in its own session. It computes the runs one at a time and commits each result together with the job's counters.

Both talk only through SQLite (`~/.planthz/planthz.db`, in WAL mode). The processing page polls `/campaigns/{id}/progress` every 2 seconds while a job is active.

**Stopping:** the page sets `stop_requested`, and the worker exits before its next run.

**A dead worker** cannot report itself. A job counts as interrupted if its process no longer exists or its heartbeat is over 5 minutes old, and whoever reads the job next closes it. Starting again resumes the work, since a job always takes whatever is still runnable.

Only one job runs at a time, across all campaigns.

## Data model

| Table | Holds | Notes |
|---|---|---|
| `campaign` | Folder and name | Also the files the last scan skipped |
| `video` | Name, path, day, capture time, `on_disk` | Unique by name within a campaign, so moving a file between folders keeps its results |
| `disabled_day` | Campaign and day | Days are enabled unless listed, so a day found by a new scan starts enabled |
| `campaign_rois` | Both plants' rectangles | One row per campaign, used for all of its videos |
| `result` | Frequency, amplitude, source or error, and **the ROI used** | One row per run |
| `job` | Status, counters, pid, heartbeat | |

Scanning never deletes a video. A video that left the disk is marked `on_disk = 0` and keeps its results, because the datasets are too large to keep whole on disk.

## Run status

Derived on every read (`store.run_status`), never stored:

| Status | Condition | Runnable | In the analysis |
|---|---|---|---|
| `done` | Has a frequency, computed with the campaign's current ROI | | Yes |
| `no_peak` | Computed with that ROI, but no peak above the threshold | | No |
| `failed` | Last attempt raised an error | Yes | No |
| `stale` | Computed with a ROI that has since changed | Yes | No |
| `pending` | Never computed | Yes | No |
| `no_roi` | The campaign has no ROIs yet | | No |
| `missing` | File is not on disk and there is no usable result | | No |

A video enters the analysis only if **both** of its runs are `done`, so the plants are always compared on the same videos.

## Disabled days

A job only takes the runs that have a runnable status **and** belong to an enabled day (`Run.runnable`). Disabling a day changes nothing else: its runs keep their status and its existing results stay in the analysis and in `results.csv`.

A day cannot change while its campaign has an active job, because the worker picks its runs when it starts. Stopping, changing the day and continuing has the same effect.

## Frames

`frames.py` caches each extracted frame at full resolution in `~/.planthz/frames`, keyed by video path and position. ROIs are drawn on each request rather than cached, so the picture always shows what the next run would crop. The positions are limited to tenths of the video to keep the cache bounded.

## Decisions

- **FastAPI + HTMX instead of Streamlit or React:** the job must outlive the page, which fits poorly with Streamlit's script reruns, and a local tool does not justify a second toolchain for React. All logic stays in Python and it runs with one command.
- **Polling instead of server-sent events:** the worker is a separate process, so the server would poll SQLite anyway to feed a stream. A run takes about 2 seconds, so polling every 2 seconds loses nothing.
- **One sequential worker:** a run takes about 2 seconds, so 306 videos take about 20 minutes. Parallel runs would add contention for the disk and the CPU, and changing the number of linear algebra threads could alter results in the last digits, which matter for comparing with earlier runs.
- **One set of ROIs per campaign:** the same rectangles apply to every video, which keeps the screen to picking a reference day, drawing and saving. A camera that moves during a campaign cannot be handled inside it: the videos after the move would need their own campaign.
- **Results store the ROI they used:** changing a ROI then invalidates exactly the affected runs, with no manual cleanup.
- **A disabled day is not a run status:** a status would hide whether a run is pending, failed or stale, which is what the user needs to see when deciding to enable the day again.
- **Plain SQLite without an ORM:** a handful of tables with one writer at a time.
- **Charts as inline SVG:** each point links to its video, which turns an odd value into a ROI check in one click.
- **ROIs drawn by the user, not detected:** this keeps the methodology of the published results. A detected ROI could vary between videos and change the comparison.
- **htmx is vendored in `static/`:** the application must work without internet access.
