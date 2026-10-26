import numpy as np
import pytest

from contract_extractor.config import OcrEngine, OcrSettings, Settings
from contract_extractor.models import Word
from contract_extractor.ocr.base import OcrEngineError
from contract_extractor.ocr.engine import OcrRunner, OcrUnavailable, build_runner, mean_confidence


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def render_gray(self, page_number: int, dpi: int) -> np.ndarray:
        self.calls.append((page_number, dpi))
        return np.full((200, 160), 255, np.uint8)


class FakeProvider:
    is_local = True

    def __init__(self, name: str, words: list[Word] | None = None, fail: bool = False) -> None:
        self.name, self.words, self.fail = name, words or [], fail

    def read(self, image: np.ndarray) -> list[Word]:
        if self.fail:
            raise OcrEngineError("boom")
        return self.words


def words(*items: tuple[str, float]) -> list[Word]:
    return [Word(text=t, bbox=(10.0 * i, 10.0, 10.0 * i + 8, 20.0), conf=c) for i, (t, c) in enumerate(items)]


GOOD = words(("Nama", 0.95), (":", 0.9), ("Budi", 0.93), ("Santoso", 0.92))
BLURRY = words(("Nma", 0.41), ("Bud1", 0.38), ("5antoso", 0.35))


def test_mean_confidence():
    assert mean_confidence([]) == 0.0
    assert mean_confidence(GOOD) == pytest.approx(0.925)


def test_first_provider_that_passes_wins():
    runner = OcrRunner([FakeProvider("a", GOOD), FakeProvider("b", GOOD)])
    outcome = runner.read(FakeRenderer(), 1, "budi santoso")
    assert (outcome.engine, outcome.passed, outcome.attempts) == ("a", True, ["a"])


def test_ladder_falls_through_to_the_next_provider():
    runner = OcrRunner([FakeProvider("a", BLURRY), FakeProvider("b", GOOD)])
    outcome = runner.read(FakeRenderer(), 1, "budi santoso")
    assert (outcome.engine, outcome.passed, outcome.attempts) == ("b", True, ["a", "b"])


def test_all_failing_returns_best_attempt_marked_failed():
    runner = OcrRunner([FakeProvider("a", BLURRY), FakeProvider("b", fail=True)])
    outcome = runner.read(FakeRenderer(), 1, "")
    assert outcome.passed is False
    assert outcome.engine == "a"
    assert outcome.attempts == ["a", "b:error"]


def test_confident_text_without_the_filename_name_fails_quality():
    runner = OcrRunner([FakeProvider("a", words(("Nama", 0.95), ("Rina", 0.95), ("Wulandari", 0.95)))])
    assert runner.read(FakeRenderer(), 1, "budi santoso").passed is False


def test_every_provider_erroring_returns_no_page():
    outcome = OcrRunner([FakeProvider("a", fail=True)]).read(FakeRenderer(), 1, "")
    assert (outcome.page, outcome.engine, outcome.passed) == (None, None, False)


def test_rough_pass_uses_scoring_dpi_and_full_read_uses_read_dpi():
    renderer = FakeRenderer()
    runner = OcrRunner([FakeProvider("a", GOOD)], scoring_dpi=150, read_dpi=300)
    runner.rough_text(renderer, 2)
    runner.read(renderer, 5, "")
    assert renderer.calls == [(2, 150), (5, 300)]


def test_cloud_fallback_is_refused_until_implemented():
    with pytest.raises(OcrUnavailable, match="cloud_fallback"):
        build_runner(Settings(ocr=OcrSettings(cloud_fallback=True)))


def test_paddle_is_refused_until_available():
    with pytest.raises(OcrUnavailable, match="PaddleOCR"):
        build_runner(Settings(ocr=OcrSettings(fallback=OcrEngine.PADDLE)))


def test_missing_tesseract_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(OcrUnavailable, match="tesseract not found"):
        build_runner(Settings())


def test_invalid_language_string_is_rejected_by_config():
    with pytest.raises(ValueError):
        OcrSettings(lang="ind;rm -rf")
