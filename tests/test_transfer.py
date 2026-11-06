import json
import re
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_web import ROI_FORM, create_campaign, first_video_id, run_batch

from planthz import store
from planthz.db import connect
from planthz.rois import Roi
from planthz.web import app

DAY = "2026-10-30"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def own_videos(videos_dir: Path, tmp_path: Path) -> Path:
    """A copy these tests can delete, standing for the folder of the machine that exports."""
    copy = tmp_path / "semana2"
    shutil.copytree(videos_dir, copy)

    return copy


def campaign_id_of(url: str) -> int:
    return int(url.rsplit("/", 1)[1])


def processed_campaign(client: TestClient, videos: Path) -> str:
    """A campaign with results, a video without peak, a disabled day and an ignored file."""
    (videos / "2026-10-29" / "no_time.mp4").touch()
    url = create_campaign(client, videos)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)

    db = connect()
    video_id = first_video_id(client, url)
    db.execute("UPDATE result SET frequency = NULL, amplitude = NULL, source = NULL WHERE video_id = ?", (video_id,))
    db.commit()
    db.close()
    client.post(f"{url}/days/{DAY}", data={"enabled": "false"})

    return url


def import_file(client: TestClient, content: bytes | str) -> Any:
    return client.post("/campaigns/import", files={"file": ("campaign.json", content)}, follow_redirects=False)


def move_to_another_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, videos: Path | None = None) -> None:
    monkeypatch.setenv("PLANTHZ_DATA_DIR", str(tmp_path / "recipient"))

    if videos is not None:
        shutil.rmtree(videos)


def main_of(page: str) -> str:
    """The page content, without the database ids, which change on import."""
    content = page.split("<main>")[1].split("</main>")[0]

    return re.sub(r"/(videos|campaigns)/\d+", r"/\1/<id>", content)


def results_table_of(page: str) -> str:
    """The results of a video page. The rest of the page depends on the file being on this machine."""
    return page.split("<h2>Resultados</h2>")[1].split("</table>")[0]


def table_rows(db: sqlite3.Connection, query: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in db.execute(query)]


def database_contents() -> dict[str, list[tuple[Any, ...]]]:
    """Every table an export carries, without the database ids and the machine's `on_disk`."""
    db = connect()
    contents = {
        "campaign": table_rows(db, "SELECT name, videos_dir, ignored_files, created_at FROM campaign"),
        "video": table_rows(db, "SELECT name, path, day, captured_at FROM video ORDER BY name"),
        "disabled_day": table_rows(db, "SELECT day FROM disabled_day"),
        "campaign_rois": [row[1:] for row in table_rows(db, "SELECT * FROM campaign_rois")],
        "result": table_rows(
            db,
            """
            SELECT video.name, result.plant_id, roi_x, roi_y, roi_w, roi_h, frequency, amplitude, source, error,
                   processed_at
            FROM result JOIN video ON video.id = result.video_id ORDER BY video.name, result.plant_id
            """,
        ),
    }
    db.close()

    return contents


def test_round_trip_keeps_everything_where_the_videos_do_not_exist(
    client: TestClient, own_videos: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = processed_campaign(client, own_videos)
    tables_before = database_contents()
    assert tables_before["result"] and tables_before["disabled_day"]
    assert "no_time.mp4" in tables_before["campaign"][0][2]
    video_id = first_video_id(client, url)
    export = client.get(f"{url}/export.json")
    csv = client.get(f"{url}/results.csv")
    analysis = client.get(f"{url}/analysis").text
    video_tables = [results_table_of(client.get(f"/videos/{video_id + offset}").text) for offset in range(3)]

    move_to_another_machine(monkeypatch, tmp_path, own_videos)
    response = import_file(client, export.content)

    assert response.status_code == 303
    new_url = response.headers["location"].removesuffix("/analysis")
    new_video_id = first_video_id(client, new_url)
    assert database_contents() == tables_before
    assert "<svg" in analysis
    assert main_of(client.get(f"{new_url}/analysis").text) == main_of(analysis)
    assert client.get(f"{new_url}/results.csv").content == csv.content
    assert client.get(f"{new_url}/results.csv").headers["content-disposition"] == csv.headers["content-disposition"]
    assert [
        results_table_of(client.get(f"/videos/{new_video_id + offset}").text) for offset in range(3)
    ] == video_tables
    assert client.get(f"{new_url}/export.json").content == export.content


def test_an_export_of_a_campaign_without_rois_or_results_comes_back_the_same(
    client: TestClient, videos_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = create_campaign(client, videos_dir)
    export = client.get(f"{url}/export.json").content

    move_to_another_machine(monkeypatch, tmp_path)
    new_url = import_file(client, export).headers["location"].removesuffix("/analysis")

    assert "Desenhe os ROIs" in client.get(new_url).text
    assert client.get(f"{new_url}/export.json").content == export


def test_results_survive_the_actions_of_a_machine_without_the_videos(
    client: TestClient, own_videos: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = processed_campaign(client, own_videos)
    export = client.get(f"{url}/export.json").content
    move_to_another_machine(monkeypatch, tmp_path, own_videos)
    new_url = import_file(client, export).headers["location"].removesuffix("/analysis")
    analysis = main_of(client.get(f"{new_url}/analysis").text)

    processing = client.get(new_url).text
    assert "não existe neste computador" in processing
    assert f'hx-post="{new_url}/jobs"' not in processing

    assert client.get(f"{new_url}/videos?day={DAY}").status_code == 200
    assert "Nenhum vídeo da campanha está no disco" in client.get(f"{new_url}/rois").text
    assert client.get(f"/videos/{first_video_id(client, new_url)}/frame.jpg").status_code == 404

    client.post(f"{new_url}/scan")
    start = client.post(f"{new_url}/jobs")

    assert "Não há execuções a processar" in start.text
    assert main_of(client.get(f"{new_url}/analysis").text) == analysis


def test_runs_that_need_the_file_turn_missing_and_keep_what_they_stored(
    client: TestClient, own_videos: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = processed_campaign(client, own_videos)
    db = connect()
    video_id = first_video_id(client, url)
    store.save_failure(db, video_id + 1, 1, Roi(0, 0, 100, 120), "Could not read")
    store.save_failure(db, video_id + 1, 2, Roi(0, 0, 10, 10), "Could not read")
    db.commit()
    db.close()
    client.post(f"{url}/rois", data={**ROI_FORM, "plant_2_h": 100})
    tables = database_contents()
    statuses_before = [line.split(",")[4] for line in client.get(f"{url}/results.csv").text.splitlines()[1:]]
    assert {"failed", "stale"} <= set(statuses_before)
    export = client.get(f"{url}/export.json").content

    move_to_another_machine(monkeypatch, tmp_path, own_videos)
    new_url = import_file(client, export).headers["location"].removesuffix("/analysis")
    statuses_after = [line.split(",")[4] for line in client.get(f"{new_url}/results.csv").text.splitlines()[1:]]

    assert database_contents() == tables
    assert statuses_after == ["missing" if status in store.RUNNABLE else status for status in statuses_before]
    page = client.get(f"/videos/{first_video_id(client, new_url) + 1}").text
    assert "Arquivo ausente" in page
    assert "Could not read" in page


def test_import_finds_the_videos_that_are_on_this_machine(
    client: TestClient, videos_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)
    export = client.get(f"{url}/export.json").content

    move_to_another_machine(monkeypatch, tmp_path)
    new_url = import_file(client, export).headers["location"].removesuffix("/analysis")

    assert "não existe neste computador" not in client.get(new_url).text
    assert client.get(f"/videos/{first_video_id(client, new_url)}/frame.jpg").status_code == 200

    client.post(f"{new_url}/rois", data={**ROI_FORM, "plant_1_h": 100})
    assert "3 resultados foram calculados" in client.get(new_url).text


def test_the_export_is_one_readable_file_without_ids_or_job_history(client: TestClient, own_videos: Path) -> None:
    url = processed_campaign(client, own_videos)

    response = client.get(f"{url}/export.json")
    export = response.json()

    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''semana2_campaign.json"
    assert export["format"] == "planthz-campaign"
    assert export["version"] == 1
    assert export["disabled_days"] == [DAY]
    assert export["rois"]["plant_1"] == {"x": 0, "y": 0, "w": 100, "h": 120}
    assert set(export["videos"][0]) == {"name", "path", "day", "captured_at", "results"}
    assert "job" not in export


def test_export_links_are_offered_on_the_campaign_and_import_on_the_new_campaign_page(
    client: TestClient, videos_dir: Path
) -> None:
    url = create_campaign(client, videos_dir)

    assert f'href="{url}/export.json"' in client.get(url).text
    assert 'action="/campaigns/import"' in client.get("/campaigns/new").text
    assert client.get("/campaigns/999/export.json").status_code == 404


def test_a_folder_name_outside_latin1_can_be_downloaded(client: TestClient, videos_dir: Path, tmp_path: Path) -> None:
    videos = tmp_path / "植物 “ã”"
    shutil.copytree(videos_dir, videos)
    url = create_campaign(client, videos)

    assert client.get(f"{url}/export.json").status_code == 200
    assert client.get(f"{url}/results.csv").status_code == 200


def test_import_refuses_a_folder_that_already_has_a_campaign(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    export = client.get(f"{url}/export.json").content

    response = import_file(client, export)

    assert response.status_code == 422
    assert "já usa a pasta" in response.text
    assert "Exclua-a antes" in response.text
    assert len(database_contents()["campaign"]) == 1


def test_import_works_again_after_deleting_the_campaign(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    export = client.get(f"{url}/export.json").content
    client.post(f"{url}/delete")

    assert import_file(client, export).status_code == 303


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\xff\xfe\x00garbage",
        "not json",
        "[1, 2]",
        '{"format": "other", "version": 1}',
        b"[" * 200_000,
    ],
)
def test_import_refuses_what_is_not_an_export(client: TestClient, content: bytes | str) -> None:
    response = import_file(client, content)

    assert response.status_code == 422
    assert "não é uma exportação" in response.text
    assert 'role="alert"' in response.text


def test_import_refuses_a_file_too_large_to_be_an_export(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, videos_dir: Path
) -> None:
    url = create_campaign(client, videos_dir)
    export = client.get(f"{url}/export.json").content
    client.post(f"{url}/delete")
    monkeypatch.setattr("planthz.transfer.MAX_SIZE", len(export) - 1)

    assert "não é uma exportação" in import_file(client, export).text


def test_import_refuses_a_newer_version(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    export = client.get(f"{url}/export.json").json()
    client.post(f"{url}/delete")

    response = import_file(client, json.dumps({**export, "version": 2}))

    assert response.status_code == 422
    assert "versão mais nova" in response.text


def _break_field(export: dict[str, Any], breakage: str) -> None:
    video = export["videos"][0]
    result = video["results"][0]

    match breakage:
        case "version":
            export["version"] = 0
        case "missing section":
            del export["videos"]
        case "wrong type":
            video["name"] = 7
        case "invalid day":
            video["day"] = "2026-13-45"
        case "day with a path":
            video["day"] = "../etc"
        case "timezone":
            video["captured_at"] = "2026-10-29T12:00:00Z"
        case "unknown plant":
            result["plant_id"] = 3
        case "not finite":
            result["frequency"] = float("inf")
        case "overlapping rois":
            export["rois"]["plant_2"] = export["rois"]["plant_1"]
        case "repeated video":
            export["videos"].append(video)
        case "repeated result":
            video["results"].append(result)
        case "repeated day":
            export["disabled_days"] = [video["day"], video["day"]]
        case "integer too large":
            result["roi"]["x"] = 2**70


@pytest.mark.parametrize(
    "breakage",
    [
        "version",
        "missing section",
        "wrong type",
        "invalid day",
        "day with a path",
        "timezone",
        "unknown plant",
        "not finite",
        "overlapping rois",
        "repeated video",
        "repeated result",
        "repeated day",
        "integer too large",
    ],
)
def test_import_refuses_a_damaged_export_and_saves_nothing(
    client: TestClient, videos_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, breakage: str
) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)
    export = client.get(f"{url}/export.json").json()
    _break_field(export, breakage)

    move_to_another_machine(monkeypatch, tmp_path)
    response = import_file(client, json.dumps(export))

    assert response.status_code == 422
    assert "incompleto ou corrompido" in response.text
    assert all(not rows for rows in database_contents().values())


def test_imported_text_is_escaped(client: TestClient, videos_dir: Path) -> None:
    url = create_campaign(client, videos_dir)
    client.post(f"{url}/rois", data=ROI_FORM)
    run_batch(url)
    export = client.get(f"{url}/export.json").json()
    client.post(f"{url}/delete")
    attack = "<script>alert(1)</script>"
    export["campaign"]["name"] = attack
    export["campaign"]["ignored_files"] = [attack]
    export["videos"][0]["name"] = attack
    export["videos"][0]["results"][0]["error"] = attack

    new_url = import_file(client, json.dumps(export)).headers["location"].removesuffix("/analysis")

    for path in (new_url, f"{new_url}/analysis", f"{new_url}/videos", f"/videos/{first_video_id(client, new_url)}"):
        assert attack not in client.get(path).text
