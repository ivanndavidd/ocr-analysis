import numpy as np
import pytest
from helpers import make_pdf

from contract_extractor.common.config import OcrEngine, OcrSettings, Settings
from contract_extractor.common.models import Word
from contract_extractor.document.ocr.base import OcrEngineError
from contract_extractor.common.layout import PageText
from contract_extractor.document.ocr.engine import (
    OcrRunner,
    OcrUnavailable,
    build_runner,
    confident_words,
    mean_confidence,
)
from contract_extractor.document.pdf.render import PageRenderer


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


class SequenceProvider(FakeProvider):
    """Answers each read with the next word list: the plain read first, then the one with table borders erased."""

    def __init__(self, name: str, *reads: list[Word]) -> None:
        super().__init__(name)
        self.reads = list(reads)

    def read(self, image: np.ndarray) -> list[Word]:
        return self.reads.pop(0)


def test_a_failing_identity_page_is_reread_with_table_borders_erased():
    runner = OcrRunner([SequenceProvider("a", BLURRY, GOOD)])
    outcome = runner.read(FakeRenderer(), 1, "budi santoso", erase_rules=True)
    assert (outcome.passed, outcome.engine, outcome.attempts) == (True, "a", ["a", "a+rules_erased"])


def test_a_passing_page_is_never_reread_with_table_borders_erased():
    provider = SequenceProvider("a", GOOD, BLURRY)
    outcome = OcrRunner([provider]).read(FakeRenderer(), 1, "budi santoso", erase_rules=True)
    assert (outcome.passed, outcome.attempts, len(provider.reads)) == (True, ["a"], 1)


def test_without_erase_rules_a_failing_page_is_read_once():
    outcome = OcrRunner([SequenceProvider("a", BLURRY, GOOD)]).read(FakeRenderer(), 1, "budi santoso")
    assert (outcome.passed, outcome.attempts) == (False, ["a"])


def test_confident_text_without_the_filename_name_fails_quality():
    runner = OcrRunner([FakeProvider("a", words(("Nama", 0.95), ("Rina", 0.95), ("Wulandari", 0.95)))])
    assert runner.read(FakeRenderer(), 1, "budi santoso").passed is False


def test_every_provider_erroring_returns_no_page():
    outcome = OcrRunner([FakeProvider("a", fail=True)]).read(FakeRenderer(), 1, "")
    assert (outcome.page, outcome.engine, outcome.passed) == (None, None, False)


def test_full_read_uses_read_dpi():
    renderer = FakeRenderer()
    OcrRunner([FakeProvider("a", GOOD)], scoring_dpi=150, read_dpi=300).read(renderer, 5, "")
    assert renderer.calls == [(5, 300)]


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


class TurnedRenderer(FakeRenderer):
    """A page photographed turned: it reads as text only at the `upright` turn, which the image width carries."""

    def __init__(self) -> None:
        super().__init__()
        self.turns: dict[int, int] = {}
        self.turns_read: list[int] = []

    def render_gray(self, page_number: int, dpi: int, turn: int | None = None) -> np.ndarray:
        turn = self.turns.get(page_number, 0) if turn is None else turn
        self.turns_read.append(turn)
        return np.full((200, 160 + turn), 255, np.uint8)


class TurnProvider(FakeProvider):
    def __init__(self, upright: int) -> None:
        super().__init__("tesseract")
        self.upright = upright

    def read(self, image: np.ndarray) -> list[Word]:
        if image.shape[1] - 160 == self.upright:
            return words(*[("Perjanjian", 0.9)] * 30)
        return words(("~i", 0.3), ("Pe", 0.5))


def _page(found: list[Word]) -> PageText:
    return PageText(number=1, width=160, height=200, words=tuple(found), source="ocr:tesseract")


def test_a_page_that_reads_upright_is_never_turned():
    renderer = TurnedRenderer()
    upright = _page(words(*[("Perjanjian", 0.9)] * 30))
    assert OcrRunner([TurnProvider(0)]).best_turn(renderer, 1, upright) == 0
    assert renderer.turns_read == []  # nothing else is read


@pytest.mark.parametrize("upright", [90, 180, 270])
def test_a_turned_photo_is_read_in_the_turn_that_reads_best(upright):
    renderer = TurnedRenderer()
    assert OcrRunner([TurnProvider(upright)]).best_turn(renderer, 1, _page(words(("~i", 0.3)))) == upright
    assert renderer.turns_read == [90, 180, 270]


def test_a_page_that_reads_badly_every_way_stays_as_it_is():
    noise = words(("Pe", 0.5))
    assert OcrRunner([FakeProvider("tesseract", noise)]).best_turn(TurnedRenderer(), 1, _page(noise)) == 0
    assert confident_words(words(("Nama", 0.9), ("Nm", 0.9), ("Santoso", 0.4))) == 1


def test_the_renderer_turns_a_page_by_its_entry(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", [("text", "Nama : Budi Santoso")])
    with PageRenderer(pdf) as plain, PageRenderer(pdf, {1: 90}) as turning:
        upright, turned = plain.render_gray(1, 72), turning.render_gray(1, 72)
        assert turned.shape == upright.shape[::-1]
        assert turning.render_rgb(1, 72).shape[:2] == turned.shape
        assert turning.render_gray(1, 72, turn=0).shape == upright.shape


def test_the_erased_read_alone_skips_the_plain_read():
    provider = SequenceProvider("a", GOOD)
    outcome = OcrRunner([provider]).read_erased(FakeRenderer(), 1, "budi santoso")
    assert (outcome.passed, outcome.attempts, provider.reads) == (True, ["a+rules_erased"], [])
