# Architecture

## Processes

- **Web server** (`web.py`): FastAPI with Jinja2 templates and HTMX. It binds to localhost only, because it reads any folder the user types.
- **Worker** (`worker.py`): one process per job, started by `jobs.py` in its own session. It computes the runs one at a time and commits each result together with the job's counters. Each run also stores its source maps and the video's first frame, so the video page can show them after the video leaves the disk.

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

Hiding a plant from a chart's legend on the analysis page only hides its marks: the axes keep the range of both plants.

The per-video chart shows points or a line. As a line it can add a moving average (`analysis.moving_average`): the mean of the plant's videos within a window of 1, 3, 6 or 24 hours centred on each video, so a 3-hour window takes the videos up to 1.5 hours before and after. The window is in time rather than in a number of videos, so a missing video narrows the sample instead of stretching the period. A day without videos breaks the line and the average. `charts.py` renders every view and window, and the browser only switches between them.

The daily chart shades one standard deviation around each plant's daily mean, and its vertical axis covers that band. Hiding the band in the legend moves the chart to an axis that covers only the means: `charts.py` renders both axes, and the browser only switches between them. A day with one video has no standard deviation and adds no height to the band. A day without videos breaks both the line and the band.

## Disabled days

A job only takes the runs that have a runnable status **and** belong to an enabled day (`Run.runnable`). Disabling a day changes nothing else: its runs keep their status and its existing results stay in the analysis and in `results.csv`.

A day cannot change while its campaign has an active job, because the worker picks its runs when it starts. Stopping, changing the day and continuing has the same effect.

## Export and import

`transfer.py` writes a campaign to one ZIP file and recreates it from that file, so results can be shared with someone on another machine. The ZIP holds `campaign.json`, below, plus `source_maps/<video>/plant_<n>.npz` for each run without error and `frames/<video>.jpg` for the first frame of each video, as far as this machine has them. The pydantic models in that module are the format of `campaign.json`, both for writing and for validating.

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
- Video files, and the frames other than the first.

**On a machine without the videos,** a run with a usable result stays `done` or `no_peak`, because `store.run_status` looks at the result before the disk. The analysis, its charts and those runs in `results.csv` therefore match the original exactly. A `failed`, `stale` or `pending` run turns `missing`, since it can only be recomputed from the file, but keeps its stored values and error. The frames, the ROI editor and jobs are unavailable until the videos appear at the same paths. A scan or a started job then finds them and marks them on disk.

**Source maps and frames:** the import validates every attachment before saving anything, and puts them in its own caches under the paths of the export, which are the cache keys. A missing attachment only leaves that map or frame unavailable. They take about 230 KB per processed video, half maps and half frame. The import also accepts a bare `campaign.json`, which is what exports were before the maps, and tells it from a ZIP by its content.

**Import is all or nothing:** it runs in one transaction, and any invalid or repeated data leaves the database unchanged. A file whose `videos_dir` already belongs to a campaign is refused, as creating that campaign by hand would be.

## Frames

`frames.py` caches each extracted frame at full resolution in `~/.planthz/frames`, keyed by video path and position. ROIs are drawn on each request rather than cached, so the picture always shows what the next run would crop. The positions are limited to tenths of the video to keep the cache bounded.

## Source maps

`source_maps.py` stores the source maps of each run in `~/.planthz/source_maps`, keyed by video path and ROI. The worker writes them in the same pass that computes the result, which adds about 0.2 s to a run of about 5 s. A result computed before that gets its maps the first time the video page asks for them, if the video is still on disk; it takes as long as a run, so the page loads them after rendering.

Only what the page shows is stored: `|weight|` and `|signal|` per source, scaled to 0–255 and compressed, about 110 KB per run instead of 1.5 MB in float32. The browser (`static/source_maps.js`) colors and animates them, so the server only sends one grayscale PNG per source and the signals.

## Decisions

- **FastAPI + HTMX instead of Streamlit or React:** the job must outlive the page, which fits poorly with Streamlit's script reruns, and a local tool does not justify a second toolchain for React. All logic stays in Python and it runs with one command.
- **Polling instead of server-sent events:** the worker is a separate process, so the server would poll SQLite anyway to feed a stream. A run takes about 2 seconds, so polling every 2 seconds loses nothing.
- **One sequential worker:** a run takes about 2 seconds, so 306 videos take about 20 minutes. Parallel runs would add contention for the disk and the CPU, and changing the number of linear algebra threads could alter results in the last digits, which matter for comparing with earlier runs.
- **One set of ROIs per campaign:** the same rectangles apply to every video, which keeps the screen to picking a reference day, drawing and saving. A camera that moves during a campaign cannot be handled inside it: the videos after the move would need their own campaign.
- **Results store the ROI they used:** changing a ROI then invalidates exactly the affected runs, with no manual cleanup.
- **A disabled day is not a run status:** a status would hide whether a run is pending, failed or stale, which is what the user needs to see when deciding to enable the day again.
- **Export without the videos:** the videos are too large to keep whole even on the sender's disk, and the person receiving them usually wants the results. A ZIP with a JSON file can be inspected, unlike a copy of the database, which would also bring every other campaign.
- **Every export brings the source maps:** without them the recipient cannot check where a frequency comes from, which is what the maps are for. The cost is size: about 230 KB per processed video instead of about 1 KB, so 60 MB for 264 videos, too large for most email attachments.
- **Exports keep the original absolute paths:** `videos_dir` and `path` are the only link back to the files, and they make an import on the same machine fully working. The cost is that the file reveals the sender's folder names, which the export panel states.
- **A folder conflict refuses the import instead of merging or renaming:** merging would have to choose between two sets of results for the same run, and a silently renamed folder would break the rule that a folder has one campaign. The user decides by deleting the existing campaign.
- **Plain SQLite without an ORM:** a handful of tables with one writer at a time.
- **Charts as inline SVG:** each point links to its video, which turns an odd value into a ROI check in one click. The legend toggles and tooltips are a small script (`static/charts.js`) over that SVG instead of a chart library, which would need a bundle and move the chart calculations out of Python.
- **Fixed vertical axis when a plant is hidden:** rescaling would make the same difference between plants look larger or smaller from one view to the next. Hiding the band is different: its axis is mostly empty space around the means, so the chart rescales to them.
- **ROIs drawn by the user, not detected:** this keeps the methodology of the published results. A detected ROI could vary between videos and change the comparison.
- **Source maps stored as files, not in SQLite:** they are only for inspection and would make the database much larger.
- **Source maps computed during processing:** the video is often deleted after processing to free space, and the maps can only be computed from it. Computing them in the same pass costs little, while computing them later would need every video kept on disk until someone opens its page.
- **The ZIP wraps `campaign.json` instead of changing its version:** exports from before the maps stay importable as they were, and the attachments, already compressed, are stored without compressing them again.
- **Source animation in the browser instead of a video file:** OpenCV writes `mp4v`, which browsers do not play. H.264 would need ffmpeg and one file per video, plant and source, while the browser needs only the map and the signal.
- **htmx is vendored in `static/`:** the application must work without internet access.
