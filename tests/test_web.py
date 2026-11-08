import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import HEIGHT, WIDTH
from fastapi.testclient import TestClient

from planthz import frames, store, worker
from planthz.db import connect
from planthz.web import app

ROI_FORM = {
    "plant_1_x": 0,
    "plant_1_y": 0,
    "plant_1_w": WIDTH // 2,
    "plant_1_h": HEIGHT,
    "plant_2_x": WIDTH // 2,
    "plant_2_y": 0,
    "plant_2_w": WIDTH // 2,
    "plant_2_h": HEIGHT,
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def create_campaign(client: TestClient, videos_dir: Path) -> str:
    response = client.post("/campaigns", data={"videos_dir": str(videos_dir)}, follow_redirects=False)
    assert response.status_code == 303

    return response.headers["location"]


def first_video_id(client: TestClient, url: str) -> int:
    page = client.get(f"{url}/videos").text

    return int(page.split('href="/videos/')[1].split('"')[0])


def run_batch(url: str) -> None:
    db = connect()
    campaign_id = int(url.rsplit("/", 1)[1])
    db.execute(
        "INSERT INTO job (campaign_id, status, total, started_at, heartbeat_at) VALUES (?, 'running', 6, 'x', 'x')",
        (campaign_id,),
    )
    db.commit()
    worker.run_job(db, db.execute("SELECT max(id) FROM job").fetchone()[0])
    db.close()


def test_rejects_a_folder_that_does_not_exist(client: TestClient) -> None:
    response = client.post("/campaigns", data={"videos_dir": "/does/not/exist"})

    assert response.status_code == 422
    assert "não existe" in response.text


def test_rejects_a_folder_without_videos(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/campaigns", data={"videos_dir": str(tmp_path)})

    assert response.status_code == 422
    assert "Nenhum vídeo" in response.text


def test_rejects_the_same_folder_twice(client: TestClient, videos_dir: Path) -> None:
    create_campaign(client, videos_dir)

    response = client.post("/campaigns", data={"videos_dir": str(videos_dir)})

    assert response.status_code == 422
    assert "Já existe" in response.text


def add_video(videos_dir: Path) -> None:
    new_day = videos_dir / "2026-12-25"
    new_day.mkdir()
    shutil.copy(next(videos_dir.rglob("*.mp4")), new_day / "video_20261225_120000.mp4")


def count_videos(url: str) -> int:
    db = connect()
    n_videos = len(store.list_videos(db, int(url.rsplit("/", 1)[1])))
    db.close()

    return n_videos


@pytest.mark.parametrize("page", ["", "/rois", "/videos", "/analysis"])
def test_campaign_pages_pick_up_new_videos_when_they_load(client: TestClient, own_videos: Path, page: str) -> None:
    url = create_campaign(client, own_videos)
    n_videos = count_videos(url)
    add_video(own_videos)

    assert client.get(f"{url}{page}").status_code == 200
    assert count_videos(url) == n_videos + 1


def test_progress_polling_does_not_scan_the_folder(client: TestClient, own_videos: Path) -> None:
    url = create_campaign(client, own_videos)
    n_videos = count_videos(url)
    add_video(own_videos)

    assert client.get(f"{url}/progress").status_code == 200
    assert count_videos(url) == n_videos


def test_sidebar_links_to_the_rois_while_a_video_is_on_disk(client: TestClient, own_videos: Path) -> None:
    url = create_campaign(client, own_videos)
    assert f'href="{url}/rois"' in client.get(url).text.split("<main>")[0]

    shutil.rmtree(own_videos)

    sidebar = client.get(url).text.split("<main>")[0]
    assert f'href="{url}/rois"' not in sidebar
    assert re.search(r'aria-disabled="true">.*?ROIs</a>', sidebar)


def test_campaign_menu_shows_the_folder_and_holds_the_downloads(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    sidebar = client.get(f"{url}/videos").text.split("<main>")[0]
    before_menu, menu = sidebar.split('<details class="campaign-menu">')

    assert str(videos_dir) in menu
    assert f"{url}/export.zip" in menu
    assert f"{url}/results.csv" in menu
    assert f"{url}/export.zip" not in before_menu
    assert f"{url}/results.csv" not in before_menu


def test_processing_page_lists_the_history_with_the_end_time(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    assert "Nenhuma execução concluída" in client.get(url).text

    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)

    page = client.get(url).text
    assert "<h2>Histórico</h2>" in page
    assert page.count('<td><a href="/videos/') == 6
    assert re.search(r"<td class=\"num\">\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}</td>", page)


def test_static_files_are_revalidated_by_the_browser(client: TestClient) -> None:
    response = client.get("/static/processing.js")

    assert response.headers["cache-control"] == "no-cache"


def test_processing_page_asks_for_rois_first(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    page = client.get(url).text

    assert "Desenhe os ROIs" in page
    assert "Iniciar" not in page


def test_saving_rois_confirms_on_the_page_it_returns_to(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    video_id = first_video_id(client, url)

    response = client.post(f"{url}/rois", data={**ROI_FORM, "reference_video": video_id})

    assert "ROIs salvos" in response.text
    assert "ROIs salvos" not in client.get(f"{url}/rois?video={video_id}").text


def test_saving_overlapping_rois_is_rejected_and_keeps_the_input(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    video_id = first_video_id(client, url)

    response = client.post(
        f"{url}/rois", data={**ROI_FORM, "plant_2_x": 10, "reference_video": video_id}, follow_redirects=False
    )

    assert response.status_code == 422
    assert "sobrepor" in response.text
    assert 'value="10"' in response.text


def test_saving_rois_larger_than_the_frame_is_rejected(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    video_id = first_video_id(client, url)

    response = client.post(
        f"{url}/rois", data={**ROI_FORM, "plant_2_w": 5000, "reference_video": video_id}, follow_redirects=False
    )

    assert response.status_code == 422
    assert "caber no quadro" in response.text


def test_frame_with_and_without_rois_differ(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    video_id = first_video_id(client, url)
    client.post(f"{url}/rois", data=ROI_FORM)

    with_overlay = client.get(f"/videos/{video_id}/frame.jpg")
    without_overlay = client.get(f"/videos/{video_id}/frame.jpg?overlay=false")
    small = client.get(f"/videos/{video_id}/frame.jpg?width=160&position=5")

    assert with_overlay.status_code == 200
    assert with_overlay.headers["content-type"] == "image/jpeg"
    assert with_overlay.content != without_overlay.content
    assert small.status_code == 200


def test_frame_position_outside_the_range_is_refused(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    video_id = first_video_id(client, url)

    assert client.get(f"/videos/{video_id}/frame.jpg?position=11").status_code == 422


def test_full_flow_from_rois_to_analysis(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    assert "Nenhum ROI" in client.get(f"{url}/videos").text
    assert "Ainda não há vídeos" in client.get(f"{url}/analysis").text

    client.post(f"{url}/rois", data=ROI_FORM)
    assert "Iniciar" in client.get(url).text

    run_batch(url)

    processing = client.get(url).text
    assert "Concluído" in processing
    assert "Iniciar" not in processing

    assert "Hz" in client.get(f"{url}/videos").text

    analysis = client.get(f"{url}/analysis").text
    assert "plant-card" in analysis
    assert "3 vídeos" in analysis
    assert "<svg" in analysis

    video_id = first_video_id(client, url)
    video_page = client.get(f"/videos/{video_id}").text
    assert "Concluída" in video_page
    assert "(0, 0, 100, 120)" in video_page

    export = client.get(f"{url}/results.csv")
    assert export.headers["content-type"].startswith("text/csv")
    assert len(export.text.splitlines()) == 7


def test_analysis_compares_with_another_campaign(client: TestClient, videos_dir: Path, own_videos: Path) -> None:
    url = create_campaign(client, videos_dir)
    other_url = create_campaign(client, own_videos)
    client.post(f"{url}/rois", data=ROI_FORM)
    assert 'name="compare"' not in client.get(f"{url}/analysis").text

    run_batch(url)
    assert 'name="compare"' not in client.get(f"{url}/analysis").text

    client.post(f"{other_url}/rois", data=ROI_FORM)
    run_batch(other_url)
    other_id = other_url.rsplit("/", 1)[1]

    alone = client.get(f"{url}/analysis?compare=").text
    assert 'name="compare"' in alone
    assert "data-compared" not in alone

    compared = client.get(f"{url}/analysis?compare={other_id}").text
    assert f'value="{other_id}" selected' in compared
    assert "Comparada a semana2" in compared
    assert 'aria-label="igual"' in compared
    assert "data-compared" in compared
    assert "Dia da campanha" in compared


def test_source_maps_explain_the_stored_result(client: TestClient, own_videos: Path) -> None:
    url = create_campaign(client, own_videos)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)
    video_id = first_video_id(client, url)

    assert 'class="source-origin"' in client.get(f"/videos/{video_id}").text

    sources = client.get(f"/videos/{video_id}/plants/1/sources.json").json()
    assert len(sources["sources"]) == 10
    result = store.list_runs(connect(), int(url.rsplit("/", 1)[1]))[0].result
    assert result is not None
    assert sources["dominant"] == result.source
    assert max(sources["sources"][0]["signal"]) == 1

    png = client.get(f"/videos/{video_id}/plants/1/sources/{sources['dominant']}.png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert client.get(f"/videos/{video_id}/plants/1/sources/10.png").status_code == 404


def test_processing_keeps_the_source_maps_and_frames_for_when_the_video_leaves_the_disk(
    client: TestClient, own_videos: Path, data_dir: Path
) -> None:
    url = create_campaign(client, own_videos)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)
    video_id = first_video_id(client, url)

    for video in own_videos.rglob("*.mp4"):
        video.unlink()

    for position in range(frames.MAX_POSITION + 1):
        assert client.get(f"/videos/{video_id}/frame.jpg?position={position}").status_code == 200
    # The source maps are placed in video pixels, which the reduced frames no longer give.
    assert f'data-width="{WIDTH}" data-height="{HEIGHT}"' in client.get(f"/videos/{video_id}").text

    for plant_id in (1, 2):
        assert len(client.get(f"/videos/{video_id}/plants/{plant_id}/sources.json").json()["sources"]) == 10

    shutil.rmtree(data_dir / "source_maps")
    assert client.get(f"/videos/{video_id}/plants/1/sources.json").status_code == 404


def test_source_maps_need_a_result(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    video_id = first_video_id(client, url)

    assert 'class="source-origin"' not in client.get(f"/videos/{video_id}").text
    assert client.get(f"/videos/{video_id}/plants/1/sources.json").status_code == 404


def test_changing_a_roi_is_reported_on_the_processing_page(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)

    client.post(f"{url}/rois", data={**ROI_FORM, "plant_1_h": HEIGHT - 10})

    assert "6 resultados" not in client.get(url).text
    assert "3 resultados foram calculados" in client.get(url).text


def test_disabling_a_day_updates_what_is_left_to_process(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    assert "6 execuções a processar" in client.get(url).text

    disabled = client.post(f"{url}/days/2026-10-29", data={"enabled": "false"})

    assert disabled.status_code == 200
    assert "2 execuções a processar" in disabled.text
    assert 'class="day-disabled"' in disabled.text

    enabled = client.post(f"{url}/days/2026-10-29", data={"enabled": "true"})

    assert "6 execuções a processar" in enabled.text
    assert 'class="day-disabled"' not in enabled.text


def test_stale_results_of_a_disabled_day_are_not_announced_as_redone(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)
    client.post(f"{url}/rois", data={**ROI_FORM, "plant_1_h": HEIGHT - 10})

    page = client.post(f"{url}/days/2026-10-29", data={"enabled": "false"}).text

    assert "1 resultado foi calculado" in page


def test_a_day_cannot_change_during_a_job(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    db = connect()
    now = store.now()
    db.execute(
        "INSERT INTO job (campaign_id, status, pid, total, started_at, heartbeat_at) VALUES (?, 'running', ?, 6, ?, ?)",
        (int(url.rsplit("/", 1)[1]), os.getpid(), now, now),
    )
    db.commit()
    db.close()

    response = client.post(f"{url}/days/2026-10-29", data={"enabled": "false"})

    assert "Pare o processamento" in response.text
    assert 'class="day-disabled"' not in response.text
    assert "disabled title=" in response.text


def test_a_day_without_videos_cannot_be_disabled(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    assert client.post(f"{url}/days/2020-01-01", data={"enabled": "false"}).status_code == 404


def test_saving_rois_again_replaces_them(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)

    client.post(f"{url}/rois", data={**ROI_FORM, "plant_1_h": HEIGHT - 10})

    assert f'value="{HEIGHT - 10}"' in client.get(f"{url}/rois").text
    assert "6 execuções a processar" in client.get(url).text


def test_deleting_a_campaign_removes_it(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    client.post(f"{url}/delete")

    assert client.get(url).status_code == 404


def test_a_campaign_cannot_be_deleted_during_its_job(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    db = connect()
    now = store.now()
    db.execute(
        "INSERT INTO job (campaign_id, status, pid, total, started_at, heartbeat_at) VALUES (?, 'running', ?, 6, ?, ?)",
        (int(url.rsplit("/", 1)[1]), os.getpid(), now, now),
    )
    db.commit()
    db.close()

    response = client.post(f"{url}/delete")

    assert response.status_code == 409
    assert "Pare o processamento" in response.text
    assert client.get(url).status_code == 200


def test_home_opens_the_new_campaign_form_without_campaigns(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/campaigns/new"


def test_home_opens_a_campaign_when_there_is_one(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == url


def test_home_returns_to_the_form_after_deleting_the_last_campaign(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    response = client.post(f"{url}/delete")

    assert response.url.path == "/campaigns/new"


def test_new_campaign_page_has_no_sidebar_but_lists_the_campaigns(client: TestClient, videos_dir: Path) -> None:
    assert 'class="sidebar"' not in client.get("/campaigns/new").text

    url = create_campaign(client, videos_dir)
    page = client.get("/campaigns/new").text

    assert 'class="sidebar"' not in page
    assert f'<a href="{url}">' in page


def test_sidebar_offers_the_campaign_actions(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    for path in (url, f"{url}/rois", f"{url}/videos", f"{url}/analysis"):
        page = client.get(path).text
        assert f'<a href="{url}" aria-current="page">' in page or f'<a href="{url}" >' in page
        assert '<a href="/campaigns/new">Nova campanha</a>' in page
        assert f'action="{url}/delete"' in page
