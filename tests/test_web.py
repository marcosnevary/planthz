import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import HEIGHT, WIDTH
from fastapi.testclient import TestClient

from planthz import store, worker
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


def test_processing_page_asks_for_rois_first(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)

    page = client.get(url).text

    assert "Desenhe os ROIs" in page
    assert "Iniciar" not in page


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
    assert "Resumo por planta" in analysis
    assert "<svg" in analysis

    video_id = first_video_id(client, url)
    video_page = client.get(f"/videos/{video_id}").text
    assert "Concluída" in video_page
    assert "(0, 0, 100, 120)" in video_page

    export = client.get(f"{url}/results.csv")
    assert export.headers["content-type"].startswith("text/csv")
    assert len(export.text.splitlines()) == 7


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
