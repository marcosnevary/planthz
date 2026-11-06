from datetime import datetime
from pathlib import Path

from planthz.scan import parse_capture_time, scan_videos


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_finds_day_folders_at_any_depth_sorted_by_capture_time(tmp_path: Path) -> None:
    touch(tmp_path / "semana2" / "2026-10-30" / "semana2_20261030_080000.mp4")
    touch(tmp_path / "2026-10-29" / "x_20261029_120000.MP4")

    result = scan_videos(tmp_path)

    assert [video.name for video in result.videos] == ["x_20261029_120000.MP4", "semana2_20261030_080000.mp4"]
    assert result.videos[0].day == "2026-10-29"
    assert result.videos[0].captured_at == datetime(2026, 10, 29, 12, 0, 0)
    assert result.ignored == []


def test_root_can_be_the_day_folder_itself(tmp_path: Path) -> None:
    touch(tmp_path / "2026-10-29" / "x_20261029_120000.mp4")

    assert len(scan_videos(tmp_path / "2026-10-29").videos) == 1


def test_follows_symlinks(tmp_path: Path) -> None:
    touch(tmp_path / "real" / "x_20261029_120000.mp4")
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "2026-10-29").symlink_to(tmp_path / "real")

    assert len(scan_videos(tmp_path / "root").videos) == 1


def test_reports_videos_it_cannot_place(tmp_path: Path) -> None:
    touch(tmp_path / "loose_20261029_120000.mp4")
    touch(tmp_path / "2026-10-29" / "no_timestamp.mp4")
    touch(tmp_path / "2026-10-29" / "x_20261029_120000.mp4")
    touch(tmp_path / "2026-10-30" / "x_20261029_120000.mp4")

    result = scan_videos(tmp_path)

    assert len(result.videos) == 1
    assert len(result.ignored) == 3


def test_skips_companion_files_silently(tmp_path: Path) -> None:
    touch(tmp_path / "2026-10-29" / "._x_20261029_120000.mp4")
    touch(tmp_path / "2026-10-29" / "x_20261029_120000_C0001.XML")

    result = scan_videos(tmp_path)

    assert result.videos == []
    assert result.ignored == []


def test_invalid_timestamp_is_not_a_capture_time() -> None:
    assert parse_capture_time("x_20261399_999999.mp4") is None
