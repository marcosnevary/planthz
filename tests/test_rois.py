from planthz.rois import Roi, validate_plants


def test_touching_rois_do_not_overlap() -> None:
    assert validate_plants({1: Roi(0, 0, 10, 10), 2: Roi(10, 0, 10, 10)}) is None


def test_overlapping_rois_are_rejected() -> None:
    assert validate_plants({1: Roi(0, 0, 11, 10), 2: Roi(10, 0, 10, 10)}) is not None


def test_undrawn_roi_is_rejected() -> None:
    assert validate_plants({1: Roi(0, 0, 0, 0), 2: Roi(10, 0, 10, 10)}) == "Desenhe o ROI da Planta 1."


def test_fits_in_frame() -> None:
    assert Roi(1080, 0, 820, 1070).fits_in(1920, 1080)
    assert not Roi(1080, 0, 841, 1070).fits_in(1920, 1080)
