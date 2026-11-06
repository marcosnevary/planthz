# planthz

Local web application that estimates the dominant oscillation frequency of two plants from fixed-camera videos.

Point it at a folder of videos separated by day, draw the region of interest (ROI) of each plant, process the batch while following its progress, check the ROI over each video, and read the analyses per plant.

## Requirements

- [uv](https://docs.astral.sh/uv/)

## Running

```bash
uv run planthz
```

Then open <http://127.0.0.1:8000>. Use `--port` to change the port and `--reload` while developing.

## Video folder

Videos must be `.mp4` files inside `YYYY-MM-DD` folders, at any depth, with the capture time in the file name as `YYYYMMDD_HHMMSS`:

```
campaign/
├── 2026-10-29/
│   ├── video_20261029_120503.mp4
│   └── video_20261029_123503.mp4
└── week2/
    └── 2026-10-30/
        └── video_20261030_000503.mp4
```

Each frame must show both plants, with plant 1 on the left and plant 2 on the right. Files that do not follow this layout are skipped and listed on the processing page.

## Workflow

1. **New campaign:** with no campaigns, the app opens on the new campaign page, where you enter the path of the video folder. Otherwise it opens on the most recent campaign, and the sidebar's campaign menu switches campaigns, creates a new one ("Nova campanha") and deletes the current one.
2. **ROIs:** pick a reference day and drag over its frame to draw each plant's rectangle, or type its coordinates. The saved ROIs apply to all videos of the campaign.
3. **Processing:** disable the days you want to skip, then start the batch. It keeps running if the page or the server closes, and it can be stopped and continued.
4. **Videos:** browse the frames of each day with the ROIs drawn on them.
5. **Analyses:** read the tables and charts, or download `results.csv`.

## Data

Campaigns, ROIs, results, job logs and cached frames live in `~/.planthz`. Set `PLANTHZ_DATA_DIR` to use another folder. The videos are only read.

## Development

```bash
uv run ruff format .
```

```bash
uv run ruff check .
```

```bash
uv run mypy
```

```bash
uv run pytest
```

## Documentation

- [docs/architecture.md](docs/architecture.md): processes, data model and run statuses.
- [docs/methodology.md](docs/methodology.md): how a frequency is estimated, and its limitations.
- [GLOSSARY.md](GLOSSARY.md): domain terms.
