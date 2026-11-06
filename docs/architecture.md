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
| `run_history` | Every result ever written, with its ROI and end time | Append-only, filled by a trigger on `result`, which keeps only each run's last attempt. Shown as "Histórico" on the processing page |
| `job` | Status, counters, pid, heartbeat | |

Every campaign page rescans the folder when it loads, as does starting a job; the progress polling does not, since it runs every 2 seconds. Scanning never deletes a video. A video that left the disk is marked `on_disk = 0` and keeps its results, because the datasets are too large to keep whole on disk.

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

## Export and import

`transfer.py` writes a campaign to one JSON file and recreates it from that file, so results can be shared with someone on another machine. The pydantic models in that module are the format, both for writing and for validating.

```json
{
  "format": "planthz-campaign",
  "version": 1,
  "campaign": {"name": "…", "videos_dir": "/abs/path", "ignored_files": [], "created_at": "2026-10-29T12:00:00"},
  "rois": {"plant_1": {"x": 0, "y": 0, "w": 100, "h": 120}, "plant_2": {"x": 100, "y": 0, "w": 100, "h": 120}},
  "disabled_days": ["2026-10-30"],
  "videos": [
    {
      "name": "video_20261029_120000.mp4",
      "path": "/abs/path/2026-10-29/video_20261029_120000.mp4",
      "day": "2026-10-29",
      "captured_at": "2026-10-29T12:00:00",
      "results": [
        {
          "plant_id": 1,
          "roi": {"x": 0, "y": 0, "w": 100, "h": 120},
          "frequency": 3.0,
          "amplitude": 12.5,
          "source": 0,
          "error": null,
          "processed_at": "2026-10-29T13:00:00"
        }
      ]
    }
  ]
}
```

`rois` is `null` when the campaign has none. A video has one result per run that was computed, so `results` can hold fewer than two or be empty. Change `VERSION` when an existing reader could no longer understand the file. A file with a higher version is refused and asks the user to update.

**Left out on purpose:**

- Database ids, because each machine assigns its own. Videos are identified by name, as they are by a scan.
- `on_disk`, which describes one machine. An import checks each video's `path` here instead.
- The `job` table: a pid and a heartbeat mean nothing elsewhere, and its logs stay in the data folder.
- `run_history`: nothing computed from a campaign depends on it, so the format stays at this version. Each imported result becomes the first entry of its run's history.
- Video files and cached frames.

**On a machine without the videos,** a run with a usable result stays `done` or `no_peak`, because `store.run_status` looks at the result before the disk. The analysis, its charts and those runs in `results.csv` therefore match the original exactly. A `failed`, `stale` or `pending` run turns `missing`, since it can only be recomputed from the file, but keeps its stored values and error. The frames, the ROI editor and jobs are unavailable until the videos appear at the same paths. A scan or a started job then finds them and marks them on disk.

**Import is all or nothing:** it runs in one transaction, and any invalid or repeated data leaves the database unchanged. A file whose `videos_dir` already belongs to a campaign is refused, as creating that campaign by hand would be.

## Frames

`frames.py` caches each extracted frame at full resolution in `~/.planthz/frames`, keyed by video path and position. ROIs are drawn on each request rather than cached, so the picture always shows what the next run would crop. The positions are limited to tenths of the video to keep the cache bounded.

## Decisions

- **FastAPI + HTMX instead of Streamlit or React:** the job must outlive the page, which fits poorly with Streamlit's script reruns, and a local tool does not justify a second toolchain for React. All logic stays in Python and it runs with one command.
- **Polling instead of server-sent events:** the worker is a separate process, so the server would poll SQLite anyway to feed a stream. A run takes about 2 seconds, so polling every 2 seconds loses nothing.
- **One sequential worker:** a run takes about 2 seconds, so 306 videos take about 20 minutes. Parallel runs would add contention for the disk and the CPU, and changing the number of linear algebra threads could alter results in the last digits, which matter for comparing with earlier runs.
- **One set of ROIs per campaign:** the same rectangles apply to every video, which keeps the screen to picking a reference day, drawing and saving. A camera that moves during a campaign cannot be handled inside it: the videos after the move would need their own campaign.
- **Results store the ROI they used:** changing a ROI then invalidates exactly the affected runs, with no manual cleanup.
- **A disabled day is not a run status:** a status would hide whether a run is pending, failed or stale, which is what the user needs to see when deciding to enable the day again.
- **JSON export without the videos:** the videos are too large to keep whole even on the sender's disk, while the results of 306 videos take about 240 KB, and the person receiving them usually wants the results. A plain JSON file can be sent by email and inspected, unlike a copy of the database, which would also bring every other campaign.
- **Exports keep the original absolute paths:** `videos_dir` and `path` are the only link back to the files, and they make an import on the same machine fully working. The cost is that the file reveals the sender's folder names, which the export panel states.
- **A folder conflict refuses the import instead of merging or renaming:** merging would have to choose between two sets of results for the same run, and a silently renamed folder would break the rule that a folder has one campaign. The user decides by deleting the existing campaign.
- **Plain SQLite without an ORM:** a handful of tables with one writer at a time.
- **Charts as inline SVG:** each point links to its video, which turns an odd value into a ROI check in one click.
- **ROIs drawn by the user, not detected:** this keeps the methodology of the published results. A detected ROI could vary between videos and change the comparison.
- **htmx is vendored in `static/`:** the application must work without internet access.
