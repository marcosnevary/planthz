import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from planthz import analysis as analysis_module
from planthz import charts, frames, jobs, store, transfer
from planthz.db import connect
from planthz.methodology import FREQUENCY_THRESHOLD
from planthz.rois import PLANT_IDS, Roi, validate_plants
from planthz.scan import scan_videos
from planthz.store import RunStatus

PACKAGE_DIR = Path(__file__).parent


class RevalidatedStaticFiles(StaticFiles):
    """Without this header browsers guess how long to reuse a file, and pair new pages with old scripts."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"

        return response


app = FastAPI(title="planthz")
app.mount("/static", RevalidatedStaticFiles(directory=PACKAGE_DIR / "static"), name="static")


def _sidebar_context(_: Request) -> dict[str, Any]:
    db = connect()

    try:
        return {
            "all_campaigns": store.list_campaigns(db),
            "campaigns_with_videos_on_disk": store.campaigns_with_videos_on_disk(db),
        }
    finally:
        db.close()


templates = Jinja2Templates(directory=PACKAGE_DIR / "templates", context_processors=[_sidebar_context])

STATUS_LABELS = {
    RunStatus.DONE: "Concluída",
    RunStatus.NO_PEAK: f"Sem pico acima de {FREQUENCY_THRESHOLD:.0f} Hz",
    RunStatus.FAILED: "Falhou",
    RunStatus.STALE: "ROI alterado",
    RunStatus.PENDING: "Pendente",
    RunStatus.NO_ROI: "Sem ROI",
    RunStatus.MISSING: "Arquivo ausente",
}

JOB_LABELS = {
    jobs.JobStatus.RUNNING: "Processando",
    jobs.JobStatus.STOPPING: "Parando após a execução atual",
    jobs.JobStatus.COMPLETED: "Concluído",
    jobs.JobStatus.STOPPED: "Parado",
    jobs.JobStatus.INTERRUPTED: "Interrompido",
    jobs.JobStatus.FAILED: "Falhou",
}


def decimal(value: float | None, decimals: int = 3) -> str:
    if value is None or value != value:
        return "–"

    return f"{value:.{decimals}f}".replace(".", ",")


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "calculando…"

    minutes = round(seconds / 60)

    if minutes < 1:
        return "menos de 1 min"

    hours, minutes = divmod(minutes, 60)

    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


templates.env.filters["decimal"] = decimal
templates.env.filters["duration"] = duration
templates.env.filters["day"] = lambda day: f"{day[8:10]}/{day[5:7]}/{day[:4]}"
templates.env.filters["moment"] = lambda moment: datetime.fromisoformat(moment).strftime("%d/%m/%Y %H:%M:%S")
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["JOB_LABELS"] = JOB_LABELS
templates.env.globals["RunStatus"] = RunStatus
templates.env.globals["PLANT_IDS"] = PLANT_IDS


def get_db() -> Iterator[sqlite3.Connection]:
    db = connect()

    try:
        yield db
    finally:
        db.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _attachment(campaign: sqlite3.Row, suffix: str) -> dict[str, str]:
    # Percent-encoded because a folder name can hold anything, and a header only takes Latin-1.
    file_name = quote(f"{Path(campaign['videos_dir']).name}_{suffix}", safe="")

    return {"Content-Disposition": f"attachment; filename*=UTF-8''{file_name}"}


def require_campaign(db: sqlite3.Connection, campaign_id: int) -> sqlite3.Row:
    campaign = store.get_campaign(db, campaign_id)

    if campaign is None:
        raise HTTPException(404, "Campanha não encontrada.")

    return campaign


def require_synced_campaign(db: sqlite3.Connection, campaign_id: int) -> sqlite3.Row:
    require_campaign(db, campaign_id)
    store.sync_videos(db, campaign_id)

    return require_campaign(db, campaign_id)


def require_video(db: sqlite3.Connection, video_id: int) -> store.Video:
    video = store.get_video(db, video_id)

    if video is None:
        raise HTTPException(404, "Vídeo não encontrado.")

    return video


# --- Campaigns ---------------------------------------------------------------------------------


@app.get("/")
def home(db: Db) -> Response:
    campaigns = store.list_campaigns(db)
    url = f"/campaigns/{campaigns[0]['id']}" if campaigns else "/campaigns/new"

    return RedirectResponse(url, status_code=303)


@app.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign(request: Request, db: Db) -> Response:
    return _new_campaign_page(request, db)


def _new_campaign_page(
    request: Request,
    db: sqlite3.Connection,
    error: str | None = None,
    videos_dir: str = "",
    import_error: str | None = None,
) -> Response:
    campaigns = []

    for campaign in store.list_campaigns(db):
        counts = store.status_counts(store.list_runs(db, campaign["id"]))
        campaigns.append(
            {
                "campaign": campaign,
                "n_videos": len(store.list_videos(db, campaign["id"])),
                "n_done": counts[RunStatus.DONE] + counts[RunStatus.NO_PEAK],
                "n_runs": sum(counts.values()),
            }
        )

    return templates.TemplateResponse(
        request,
        "new_campaign.html",
        {"campaigns": campaigns, "error": error, "videos_dir": videos_dir, "import_error": import_error},
        status_code=422 if error or import_error else 200,
    )


@app.post("/campaigns")
def create_campaign(
    request: Request, db: Db, videos_dir: Annotated[str, Form()], name: Annotated[str, Form()] = ""
) -> Response:
    path = Path(videos_dir.strip()).expanduser()

    if not path.is_absolute() or not path.is_dir():
        return _new_campaign_page(request, db, "Essa pasta não existe. Informe o caminho completo.", videos_dir)

    path = path.resolve()

    if db.execute("SELECT 1 FROM campaign WHERE videos_dir = ?", (str(path),)).fetchone():
        return _new_campaign_page(request, db, "Já existe uma campanha para essa pasta.", videos_dir)

    if not scan_videos(path).videos:
        return _new_campaign_page(
            request,
            db,
            "Nenhum vídeo encontrado. Eles devem estar em pastas por dia, como 2026-10-29/video_20261029_120503.mp4.",
            videos_dir,
        )

    campaign_id = store.create_campaign(db, name.strip() or path.name, path)
    store.sync_videos(db, campaign_id)

    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@app.post("/campaigns/import")
def import_campaign(request: Request, db: Db, file: Annotated[UploadFile, File()]) -> Response:
    try:
        campaign_id = transfer.import_campaign(db, file.file.read(transfer.MAX_SIZE + 1))
    except transfer.ImportRejected as rejected:
        return _new_campaign_page(request, db, import_error=str(rejected))

    # The results are what an import brings, and its videos are usually on another machine.
    return RedirectResponse(f"/campaigns/{campaign_id}/analysis", status_code=303)


@app.get("/campaigns/{campaign_id}/export.json")
def export_campaign(db: Db, campaign_id: int) -> Response:
    campaign = require_campaign(db, campaign_id)

    return Response(
        transfer.export_campaign(db, campaign_id),
        media_type="application/json",
        headers=_attachment(campaign, "campaign.json"),
    )


@app.post("/campaigns/{campaign_id}/delete")
def delete_campaign(request: Request, db: Db, campaign_id: int) -> Response:
    active = jobs.active_job(db)

    if active is not None and active["campaign_id"] == campaign_id:
        return _processing_page(
            request, db, require_campaign(db, campaign_id), "Pare o processamento antes de excluir a campanha."
        )

    store.delete_campaign(db, campaign_id)

    return RedirectResponse("/", status_code=303)


# --- Processing --------------------------------------------------------------------------------


def _progress_context(db: sqlite3.Connection, campaign: sqlite3.Row, error: str | None = None) -> dict[str, Any]:
    runs = store.list_runs(db, campaign["id"])
    counts = store.status_counts(runs)

    by_day: dict[str, Counter[RunStatus]] = defaultdict(Counter)
    videos_by_day: dict[str, set[int]] = defaultdict(set)

    for run in runs:
        by_day[run.video.day][run.status] += 1
        videos_by_day[run.video.day].add(run.video.id)

    job = jobs.latest_job(db, campaign["id"])
    is_active = job is not None and job["status"] in jobs.ACTIVE
    other_job = jobs.active_job(db)
    disabled = store.disabled_days(db, campaign["id"])

    return {
        "campaign": campaign,
        "counts": counts,
        "n_runs": len(runs),
        "n_runnable": sum(run.runnable for run in runs),
        "n_stale_to_redo": sum(run.runnable and run.status == RunStatus.STALE for run in runs),
        "days": [
            {"day": day, "counts": by_day[day], "n_videos": len(videos_by_day[day]), "enabled": day not in disabled}
            for day in sorted(by_day)
        ],
        "job": job,
        "job_active": is_active,
        "job_percent": round(100 * job["done"] / job["total"]) if job and job["total"] else 0,
        "seconds_left": jobs.seconds_left(job) if job and is_active else None,
        "blocked_by_other_job": other_job is not None and other_job["campaign_id"] != campaign["id"],
        "has_rois": store.get_rois(db, campaign["id"]) is not None,
        "history": store.run_history(db, campaign["id"]),
        "error": error,
    }


@app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def processing_page(request: Request, db: Db, campaign_id: int) -> Response:
    return _processing_page(request, db, require_synced_campaign(db, campaign_id))


def _processing_page(
    request: Request, db: sqlite3.Connection, campaign: sqlite3.Row, delete_error: str | None = None
) -> Response:
    return templates.TemplateResponse(
        request,
        "processing.html",
        {
            **_progress_context(db, campaign),
            "tab": "processing",
            "ignored": store.ignored_files(campaign),
            "delete_error": delete_error,
        },
        status_code=409 if delete_error else 200,
    )


@app.get("/campaigns/{campaign_id}/progress", response_class=HTMLResponse)
def progress_fragment(request: Request, db: Db, campaign_id: int) -> Response:
    campaign = require_campaign(db, campaign_id)

    return templates.TemplateResponse(request, "_progress.html", _progress_context(db, campaign))


@app.post("/campaigns/{campaign_id}/jobs", response_class=HTMLResponse)
def start_job(request: Request, db: Db, campaign_id: int) -> Response:
    campaign = require_campaign(db, campaign_id)
    error = None

    try:
        jobs.start_job(db, campaign_id)
    except jobs.JobConflict as conflict:
        error = str(conflict)

    return templates.TemplateResponse(request, "_progress.html", _progress_context(db, campaign, error))


@app.post("/campaigns/{campaign_id}/jobs/stop", response_class=HTMLResponse)
def stop_job(request: Request, db: Db, campaign_id: int) -> Response:
    campaign = require_campaign(db, campaign_id)
    jobs.request_stop(db, campaign_id)

    return templates.TemplateResponse(request, "_progress.html", _progress_context(db, campaign))


@app.post("/campaigns/{campaign_id}/days/{day}", response_class=HTMLResponse)
def set_day_enabled(request: Request, db: Db, campaign_id: int, day: str, enabled: Annotated[bool, Form()]) -> Response:
    campaign = require_campaign(db, campaign_id)

    if not store.list_videos(db, campaign_id, day):
        raise HTTPException(404, "Dia não encontrado.")

    active = jobs.active_job(db)
    error = None

    # The worker picks its runs when it starts, so a change during a job would not be honored.
    if active is not None and active["campaign_id"] == campaign_id:
        error = "Pare o processamento antes de ativar ou desativar um dia."
    else:
        store.set_day_enabled(db, campaign_id, day, enabled)

    return templates.TemplateResponse(request, "_progress.html", _progress_context(db, campaign, error))


# --- ROIs --------------------------------------------------------------------------------------


def _rois_page(
    request: Request,
    db: sqlite3.Connection,
    campaign: sqlite3.Row,
    video_id: int | None,
    error: str | None = None,
    submitted: dict[str, Any] | None = None,
) -> Response:
    on_disk = [video for video in store.list_videos(db, campaign["id"]) if video.on_disk]
    reference = next((video for video in on_disk if video.id == video_id), on_disk[0] if on_disk else None)

    if reference is None:
        return RedirectResponse(f"/campaigns/{campaign['id']}", status_code=303)

    form = submitted if submitted is not None else _rois_form(store.get_rois(db, campaign["id"]))

    first_per_day: dict[str, store.Video] = {}

    for video in on_disk:
        first_per_day.setdefault(video.day, video)

    return templates.TemplateResponse(
        request,
        "rois.html",
        {
            "campaign": campaign,
            "tab": "rois",
            "reference": reference,
            "reference_days": list(first_per_day.values()),
            "form": form,
            "error": error,
        },
        status_code=422 if error else 200,
    )


def _rois_form(plants: dict[int, Roi] | None) -> dict[str, Any]:
    form: dict[str, Any] = {}

    for plant_id in PLANT_IDS:
        roi = plants[plant_id] if plants else Roi(0, 0, 0, 0)
        form |= {
            f"plant_{plant_id}_x": roi.x,
            f"plant_{plant_id}_y": roi.y,
            f"plant_{plant_id}_w": roi.w,
            f"plant_{plant_id}_h": roi.h,
        }

    return form


@app.get("/campaigns/{campaign_id}/rois", response_class=HTMLResponse)
def rois_page(request: Request, db: Db, campaign_id: int, video: int | None = None) -> Response:
    return _rois_page(request, db, require_synced_campaign(db, campaign_id), video)


@app.post("/campaigns/{campaign_id}/rois")
def save_rois(
    request: Request,
    db: Db,
    campaign_id: int,
    plant_1_x: Annotated[int, Form()],
    plant_1_y: Annotated[int, Form()],
    plant_1_w: Annotated[int, Form()],
    plant_1_h: Annotated[int, Form()],
    plant_2_x: Annotated[int, Form()],
    plant_2_y: Annotated[int, Form()],
    plant_2_w: Annotated[int, Form()],
    plant_2_h: Annotated[int, Form()],
    reference_video: Annotated[int | None, Form()] = None,
) -> Response:
    campaign = require_campaign(db, campaign_id)
    plants = {
        1: Roi(plant_1_x, plant_1_y, plant_1_w, plant_1_h),
        2: Roi(plant_2_x, plant_2_y, plant_2_w, plant_2_h),
    }
    error = validate_plants(plants)
    video = store.get_video(db, reference_video) if reference_video else None

    if error is None and video is not None:
        size = frames.frame_size(video.path)

        if size is not None and not all(roi.fits_in(*size) for roi in plants.values()):
            error = f"Os ROIs precisam caber no quadro de {size[0]}×{size[1]} pixels."

    if error is not None:
        return _rois_page(request, db, campaign, reference_video, error, _rois_form(plants))

    store.save_rois(db, campaign_id, plants)
    suffix = f"?video={reference_video}" if reference_video else ""

    return RedirectResponse(f"/campaigns/{campaign_id}/rois{suffix}", status_code=303)


# --- Videos ------------------------------------------------------------------------------------


@app.get("/campaigns/{campaign_id}/videos", response_class=HTMLResponse)
def videos_page(request: Request, db: Db, campaign_id: int, day: str | None = None) -> Response:
    campaign = require_synced_campaign(db, campaign_id)
    videos = store.list_videos(db, campaign_id)
    days = Counter(video.day for video in videos)
    selected_day = day if day in days else (min(days) if days else None)

    runs_by_video: dict[int, list[store.Run]] = defaultdict(list)

    for run in store.list_runs(db, campaign_id, selected_day):
        runs_by_video[run.video.id].append(run)

    return templates.TemplateResponse(
        request,
        "videos.html",
        {
            "campaign": campaign,
            "tab": "videos",
            "days": sorted(days.items()),
            "selected_day": selected_day,
            "videos": [video for video in videos if video.day == selected_day],
            "runs_by_video": runs_by_video,
            "has_rois": store.get_rois(db, campaign_id) is not None,
        },
    )


@app.get("/videos/{video_id}", response_class=HTMLResponse)
def video_page(request: Request, db: Db, video_id: int) -> Response:
    campaign = require_synced_campaign(db, require_video(db, video_id).campaign_id)
    video = require_video(db, video_id)
    previous_id, next_id = store.neighbor_videos(db, video)
    runs = [run for run in store.list_runs(db, video.campaign_id, video.day) if run.video.id == video_id]

    return templates.TemplateResponse(
        request,
        "video.html",
        {
            "campaign": campaign,
            "tab": "videos",
            "video": video,
            "runs": runs,
            "has_rois": store.get_rois(db, video.campaign_id) is not None,
            "previous_id": previous_id,
            "next_id": next_id,
            "max_position": frames.MAX_POSITION,
        },
    )


@app.get("/videos/{video_id}/frame.jpg")
def video_frame(
    db: Db,
    video_id: int,
    position: Annotated[int, Query(ge=0, le=frames.MAX_POSITION)] = 0,
    width: Annotated[int | None, Query(ge=160, le=3840)] = None,
    overlay: bool = True,
) -> Response:
    video = require_video(db, video_id)
    rois = store.get_rois(db, video.campaign_id) if overlay else None

    try:
        jpeg = frames.render_frame(video.path, position, rois, width)
    except frames.FrameUnavailable as unavailable:
        raise HTTPException(404, "Frame indisponível.") from unavailable

    # The ROIs drawn on the frame can change, so the browser must not keep its own copy.
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# --- Analysis ----------------------------------------------------------------------------------


@app.get("/campaigns/{campaign_id}/analysis", response_class=HTMLResponse)
def analysis_page(request: Request, db: Db, campaign_id: int) -> Response:
    campaign = require_synced_campaign(db, campaign_id)
    runs = store.list_runs(db, campaign_id)
    result = analysis_module.build_analysis(runs)
    has_data = not result.rows.empty
    daily_by_day: dict[str, dict[int, Any]] = defaultdict(dict)

    for row in result.daily_means.to_dict("records"):
        daily_by_day[str(row["day"])][int(row["plant_id"])] = row

    return templates.TemplateResponse(
        request,
        "analysis.html",
        {
            "campaign": campaign,
            "tab": "analysis",
            "has_data": has_data,
            "per_video_charts": charts.frequency_per_video(result.rows) if has_data else {},
            "daily_chart": charts.daily_means(result.daily_means) if has_data else "",
            "summary": list(result.plant_summary.itertuples()),
            "daily_rows": sorted(daily_by_day.items()),
        },
    )


@app.get("/campaigns/{campaign_id}/results.csv")
def results_csv(db: Db, campaign_id: int) -> Response:
    campaign = require_campaign(db, campaign_id)
    runs = store.list_runs(db, campaign_id)

    return Response(
        analysis_module.results_csv(runs),
        media_type="text/csv",
        headers=_attachment(campaign, "results.csv"),
    )
