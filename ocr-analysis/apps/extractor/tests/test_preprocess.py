import cv2
import numpy as np
import pytest

from contract_extractor.document.ocr.preprocess import (
    MAX_DESKEW_DEG,
    binarize,
    deskew,
    erase_rules,
    estimate_skew,
    preprocess,
    remove_shadows,
)


def text_image(width: int = 1400, height: int = 1800) -> np.ndarray:
    image = np.full((height, width), 255, np.uint8)
    for i in range(14):
        cv2.putText(image, "PERJANJIAN KERJA WAKTU TERTENTU PIHAK KEDUA", (80, 140 + i * 110),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.3, 0, 3)
    return image


def rotate(image: np.ndarray, degrees: float) -> np.ndarray:
    h, w = image.shape
    return cv2.warpAffine(image, cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0), (w, h), borderValue=255)


@pytest.mark.parametrize("degrees", [-6.0, -2.0, 0.0, 3.0, 8.0])
def test_estimate_skew_recovers_rotation(degrees):
    assert estimate_skew(rotate(text_image(), degrees)) == pytest.approx(degrees, abs=0.7)


def test_deskew_straightens_the_page():
    crooked = rotate(text_image(), 4.0)
    assert abs(estimate_skew(deskew(crooked, estimate_skew(crooked)))) < 0.5


def test_implausible_angles_are_left_alone():
    image = text_image()
    assert deskew(image, MAX_DESKEW_DEG + 5) is image


def test_blank_page_has_no_skew():
    assert estimate_skew(np.full((800, 600), 255, np.uint8)) == 0.0


def test_remove_shadows_flattens_a_lighting_gradient():
    gradient = np.tile(np.linspace(110, 250, 1400).astype(np.uint8), (1800, 1))
    shaded = np.minimum(text_image(), gradient)
    background = text_image() == 255

    flat = remove_shadows(shaded)
    assert flat[background].std() < shaded[background].std() / 3
    assert flat[~background].mean() < flat[background].mean() - 100  # text stays dark


def test_binarize_outputs_only_black_and_white():
    assert set(np.unique(binarize(text_image()))) <= {0, 255}


def test_erase_rules_removes_table_borders_and_keeps_the_letters():
    letters = binarize(text_image())
    ruled = letters.copy()
    for i in range(15):  # a border under every row, as in a ruled identity table
        cv2.line(ruled, (60, 160 + i * 110 - 110), (1360, 160 + i * 110 - 110), 0, 3)
    for x in (60, 1360):
        cv2.line(ruled, (x, 50), (x, 1700), 0, 3)

    underline = (200, 1712, 350, 1714)  # under one word: shorter than a table border, so it stays
    cv2.rectangle(ruled, underline[:2], underline[2:], 0, -1)

    erased = erase_rules(ruled)
    border = (ruled == 0) & (letters == 255)
    border[underline[1]:underline[3] + 1, underline[0]:underline[2] + 1] = False
    assert (erased[border] == 255).mean() > 0.99
    assert (erased[letters == 0] == 0).mean() > 0.97  # the text itself stays
    assert (erased[underline[1]:underline[3] + 1, underline[0]:underline[2] + 1] == 0).all()


def test_erase_rules_leaves_a_page_without_lines_alone():
    letters = binarize(text_image())
    assert (erase_rules(letters) == letters).mean() > 0.999


def test_preprocess_erases_table_borders_only_when_asked():
    ruled = text_image()
    cv2.line(ruled, (60, 60), (1360, 60), 0, 3)
    kept, _ = preprocess(ruled)
    erased, _ = preprocess(ruled, erase=True)
    assert (kept[59:62, 100:1300] == 0).mean() > 0.9
    assert (erased[59:62, 100:1300] == 255).all()


def test_preprocess_reports_the_applied_angle():
    image, angle = preprocess(rotate(text_image(), 5.0))
    assert angle == pytest.approx(5.0, abs=0.7)
    assert set(np.unique(image)) <= {0, 255}
