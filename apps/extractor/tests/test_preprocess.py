import cv2
import numpy as np
import pytest

from contract_extractor.ocr.preprocess import MAX_DESKEW_DEG, binarize, deskew, estimate_skew, preprocess, remove_shadows


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


def test_preprocess_reports_the_applied_angle():
    image, angle = preprocess(rotate(text_image(), 5.0))
    assert angle == pytest.approx(5.0, abs=0.7)
    assert set(np.unique(image)) <= {0, 255}
