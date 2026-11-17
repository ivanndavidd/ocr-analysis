"""The second local reader: RapidOCR reads a scanned identity page again when the NIK or the position is unsure."""

import contextlib
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from helpers import EMPLOYEE_NIK, flow, page_text

from contract_extractor import pipeline
from contract_extractor.common.config import OcrSettings, SecondReaderSettings, Settings
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import FileResult, Level, Word
from contract_extractor.document.ocr import engine, rapid
from contract_extractor.document.ocr.base import OcrEngineError
from contract_extractor.document.ocr.engine import OcrRunner, OcrUnavailable
from contract_extractor.document.ocr.rapid import RapidOcrUnavailable, resolve_model_dir, split_line, tighten, verify_models
from contract_extractor.extractor_method.llm.base import ImageAnswer, ImageTask
from contract_extractor.pipeline import REASON_NIK_DIGITS_REPAIRED, REASON_NIK_FROM_OCR

OTHER_NIK = "3199990101900002"  # one digit off: still a valid NIK for a man born 1 January 1990
BIRTH_AND_GENDER = ("Tempat/Tanggal Lahir : Bandung, 1 Januari 1990", "Jenis Kelamin : Laki-laki")
POSITION = "PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Admin Gudang."


def scanned(*lines: str, conf: float = 0.9, source: str = "ocr:tesseract") -> PageText:
    page = page_text(1, flow(list(lines)))
    return dataclasses.replace(page, words=tuple(dataclasses.replace(w, conf=conf) for w in page.words), source=source)


class SecondRunner:
    """OcrRunner stand-in whose second reader gives `page` (None: the call failed)."""

    read_dpi = 300
    second = SimpleNamespace(name="rapidocr")

    def __init__(self, page: PageText | None) -> None:
        self.page, self.calls = page, []

    def second_read(self, renderer, number):
        self.calls.append(number)
        return self.page


@pytest.fixture(autouse=True)
def no_rendering(monkeypatch):
    monkeypatch.setattr(pipeline, "PageRenderer", lambda data, turns: contextlib.nullcontext())


def second_read(first: PageText, again: PageText | None):
    """Tesseract read `first`; the second reader reads `again`. Returns the NIK level, the result and the runner."""
    result = FileResult(file="Budi Santoso - 12 bulan.pdf")
    level = pipeline._read_nik(result, [first])
    pipeline._read_position(result, [first])
    runner = SecondRunner(again)
    level = pipeline._second_read(b"", result, pipeline._PagesRead({}, [first]), runner, level)
    return level, result, runner


def test_the_same_nik_from_both_engines_turns_an_unconfirmed_scan_green():
    level, result, runner = second_read(scanned(f"NIK : {EMPLOYEE_NIK}"), scanned(f"NIK : {EMPLOYEE_NIK}"))
    assert runner.calls == [1] and result.log.nik.pembaca_kedua == "sama" and level is Level.GREEN
    assert REASON_NIK_FROM_OCR not in result.log.alasan
    assert result.log.komponen_halaman == {1: ["rapidocr@300dpi:pembaca_kedua"]}


def test_other_digits_or_none_leave_the_nik_as_it_was():
    level, result, _ = second_read(scanned(f"NIK : {EMPLOYEE_NIK}"), scanned(f"NIK : {OTHER_NIK}"))
    assert (result.nik, result.log.nik.pembaca_kedua, level) == (EMPLOYEE_NIK, "beda", Level.YELLOW)
    assert REASON_NIK_FROM_OCR in result.log.alasan  # the website keeps it yellow

    level, result, _ = second_read(scanned(f"NIK : {EMPLOYEE_NIK}"), scanned("Nama : Budi Santoso"))
    assert (result.log.nik.pembaca_kedua, level) == ("tidak_terbaca", Level.YELLOW)


def test_a_nik_picked_from_a_longer_run_is_confirmed_by_the_same_digits():
    level, result, _ = second_read(scanned(f"NIK : 1{EMPLOYEE_NIK}", *BIRTH_AND_GENDER), scanned(f"NIK : {EMPLOYEE_NIK}"))
    assert (result.log.nik.pembaca_kedua, level) == ("sama", Level.GREEN)
    assert REASON_NIK_DIGITS_REPAIRED not in result.log.alasan


@pytest.mark.parametrize(
    "first",
    [
        scanned(f"NIK : {EMPLOYEE_NIK}", *BIRTH_AND_GENDER),  # confirmed by the printed birth date and gender already
        scanned(f"NIK : {EMPLOYEE_NIK}", source="text_layer"),  # from a text layer, not OCR
        scanned("NIK : 3171012345670001"),  # not a valid NIK: nothing to confirm
        scanned(f"NIK : {EMPLOYEE_NIK}", "Tempat/Tanggal Lahir : Bandung, 2 Februari 1991"),  # contradicted
        scanned("Nama : Budi Santoso"),  # no NIK
    ],
)
def test_a_nik_that_is_not_an_unsure_reading_is_not_read_again(first):
    level_before = pipeline._read_nik(FileResult(file="x.pdf"), [first])
    level, result, runner = second_read(first, scanned(f"NIK : {EMPLOYEE_NIK}"))
    assert runner.calls == [] and level is level_before
    assert result.log.nik.pembaca_kedua is None


def test_a_failed_second_read_confirms_nothing():
    level, result, runner = second_read(scanned(f"NIK : {EMPLOYEE_NIK}"), None)
    assert runner.calls == [1] and level is Level.YELLOW and result.log.nik.pembaca_kedua is None


def test_without_a_second_reader_nothing_changes():
    first = scanned(f"NIK : {EMPLOYEE_NIK}")
    result = FileResult(file="x.pdf")
    level = pipeline._read_nik(result, [first])
    assert pipeline._second_read(b"", result, pipeline._PagesRead({}, [first]), None, level) is Level.YELLOW
    runner = OcrRunner(providers=[SimpleNamespace(name="tesseract")])  # a runner built without one
    assert pipeline._second_read(b"", result, pipeline._PagesRead({}, [first]), runner, level) is Level.YELLOW
    assert result.log.nik.pembaca_kedua is None


def test_an_agreed_nik_is_not_sent_to_the_extractor(monkeypatch):
    monkeypatch.setattr(pipeline, "_page_image", lambda renderer, page, ocr: np.full((1100, 850), 255, np.uint8))

    class CropReader:
        name = "fake-llm"
        crops: list = []

        def read_image(self, task, png):
            self.crops.append(task)
            return ImageAnswer(nik=EMPLOYEE_NIK)

    first = scanned(f"NIK : {EMPLOYEE_NIK}")
    for again, sent in ((scanned(f"NIK : {EMPLOYEE_NIK}"), []), (scanned(f"NIK : {OTHER_NIK}"), [ImageTask.NIK])):
        level, result, _ = second_read(first, again)
        reader = CropReader()
        reader.crops = []
        pipeline._read_nik_crop(b"", result, pipeline._PagesRead({}, [first]), reader, None, level)
        assert reader.crops == sent  # the crop goes only when the two engines did not agree


def test_a_low_confidence_title_is_read_again_and_marked_when_both_engines_agree():
    _, result, runner = second_read(scanned(POSITION, conf=0.6), scanned(POSITION))
    assert result.posisi == "Admin Gudang" and result.log.posisi.pembaca_kedua == "sama" and runner.calls == [1]

    _, result, _ = second_read(scanned(POSITION, conf=0.6), scanned(POSITION.replace("Admin", "Adrnin")))
    assert result.posisi == "Admin Gudang" and result.log.posisi.pembaca_kedua == "beda"  # the reading is kept


def test_a_title_read_with_confidence_is_not_read_again():
    _, result, runner = second_read(scanned(POSITION, conf=0.95), scanned(POSITION))
    assert runner.calls == [] and result.log.posisi.pembaca_kedua is None


# --- the RapidOCR reader itself ------------------------------------------------------------------------------------


def test_a_line_is_split_into_words_with_their_share_of_the_box():
    words = split_line("NIK : 3199990101900001", [[0, 10], [220, 10], [220, 30], [0, 30]], 0.97)
    assert [w.text for w in words] == ["NIK", ":", "3199990101900001"]
    assert words[0].bbox == (0.0, 10.0, 30.0, 30.0) and words[2].bbox == (60.0, 10.0, 220.0, 30.0)
    assert {w.conf for w in words} == {0.97}


def test_word_boxes_shrink_to_their_ink():
    image = np.full((40, 100), 255, np.uint8)
    image[12:28, 20:50] = 0  # the ink of one word, inside a padded box
    [word] = tighten(image, [Word("Budi", (10.0, 5.0, 60.0, 35.0), 0.9)])
    assert word.bbox == (20.0, 12.0, 50.0, 28.0)
    [blank] = tighten(image, [Word("x", (70.0, 5.0, 90.0, 35.0), 0.9)])  # no ink: kept as it was
    assert blank.bbox == (70.0, 5.0, 90.0, 35.0)


def test_missing_or_changed_model_files_stop_the_second_reader(tmp_path, monkeypatch):
    monkeypatch.delenv("RAPIDOCR_MODEL_DIR", raising=False)
    monkeypatch.setattr(rapid, "DEFAULT_MODEL_DIR", tmp_path / "none")
    with pytest.raises(RapidOcrUnavailable, match="not found"):
        resolve_model_dir(tmp_path)
    for name in rapid.MODELS:
        (tmp_path / name).write_bytes(b"not a model")
    assert resolve_model_dir(tmp_path) == tmp_path
    monkeypatch.setenv("RAPIDOCR_MODEL_DIR", str(tmp_path))
    assert resolve_model_dir(None) == tmp_path
    with pytest.raises(RapidOcrUnavailable, match="SHA-256"):
        verify_models(tmp_path)


def test_the_worker_refuses_to_start_when_the_second_reader_cannot(tmp_path, monkeypatch):
    class FakeTesseract:
        name = "tesseract"

        def __init__(self, *args, **kwargs):
            pass

        def check(self):
            pass

    monkeypatch.setattr(engine, "TesseractOCR", FakeTesseract)
    monkeypatch.setattr(engine, "_resolve_tesseract", lambda configured: Path("tesseract"))
    monkeypatch.delenv("RAPIDOCR_MODEL_DIR", raising=False)
    monkeypatch.setattr(rapid, "DEFAULT_MODEL_DIR", tmp_path)
    assert engine.build_runner(Settings()).second is None  # off by default
    on = Settings(ocr=OcrSettings(second_reader=SecondReaderSettings(enabled=True, model_dir=tmp_path)))
    with pytest.raises(OcrUnavailable, match="RapidOCR models not found"):
        engine.build_runner(on)


MODEL_DIR = rapid.DEFAULT_MODEL_DIR
HAS_MODELS = all((MODEL_DIR / name).is_file() for name in rapid.MODELS)


@pytest.mark.skipif(not HAS_MODELS, reason="RapidOCR model files are not in apps/extractor/models/rapidocr")
def test_rapidocr_reads_a_rendered_nik_line():
    import cv2

    image = np.full((200, 1000), 255, np.uint8)
    cv2.putText(image, f"NIK : {EMPLOYEE_NIK}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    words = rapid.RapidOCRReader(MODEL_DIR, threads=1).read(image)
    assert EMPLOYEE_NIK in [w.text for w in words]
    assert all(0 <= w.bbox[0] < w.bbox[2] <= 1000 and 0 <= w.bbox[1] < w.bbox[3] <= 200 for w in words)


def test_a_failed_call_becomes_an_ocr_engine_error():
    reader = rapid.RapidOCRReader.__new__(rapid.RapidOCRReader)
    reader._ocr = lambda image, use_cls: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(OcrEngineError):
        reader.read(np.full((10, 10), 255, np.uint8))


# --- the period: only a date Tesseract's text left unread, completed with the filename's own -----------------------

PERIOD_FILE = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"
CLEAN = "Perjanjian ini berlaku efektif mulai tanggal 1 Januari 2026 sampai dengan 31 Desember 2026."
# Tesseract's reading on a synthetic photo: the end date broken over a line, with noise in between
BROKEN = ("Perjanjian ini berlaku efektif mulai tanggal 1 Januari 2026 sampai dengan 31 Tan", "PA ea Desember 2026, kecuali:")


def period_check(first: PageText, again: PageText | None):
    """Tesseract read `first`, the second reader reads `again`; the pattern check decides. Returns the level, the
    result and the runner."""
    from contract_extractor.extractor_method.pattern.filename import parse_filename

    result, parsed = FileResult(file=PERIOD_FILE), parse_filename(PERIOD_FILE)
    pages = pipeline._PagesRead({}, [first])
    pipeline._prepare_extraction(result, pages.p_id_pages, 100_000)
    runner = SecondRunner(again)
    pipeline._second_period(b"", result, parsed, pages, runner)
    level = pipeline._check_without_extractor(result, parsed, pages.p_id_pages, None, pages.second)
    return level, result, runner


def test_the_second_reader_completes_a_date_tesseract_left_unread():
    level, result, runner = period_check(scanned("Nama : Budi Santoso", *BROKEN), scanned("Nama : Budi Santoso", CLEAN))
    assert runner.calls == [1] and level is Level.GREEN
    assert (str(result.tanggal_mulai), str(result.tanggal_selesai), result.durasi_bulan) == ("2026-01-01", "2026-12-31", 12)
    assert result.log.validasi_nama_file["periode_dari"] == "pembaca_kedua"
    assert result.log.periode_regex.tanggal_selesai is None  # Tesseract's own reading is kept as it was
    assert str(result.log.periode_pembaca_kedua.tanggal_selesai) == "2026-12-31"


def test_the_second_readers_dates_printed_anywhere_also_complete_it():
    again = scanned("Nama : Budi Santoso", "Tanggal 1 Januari 2026 dan 31 Desember 2026 tercetak.")
    assert pipeline.find_period(again.text).tanggal_selesai is None  # no clear period sentence: only the lookup
    level, result, _ = period_check(scanned("Nama : Budi Santoso", *BROKEN), again)
    assert level is Level.GREEN and result.log.validasi_nama_file["periode_dari"] == "pembaca_kedua"


@pytest.mark.parametrize(
    "again",
    [
        scanned("Nama : Budi Santoso", CLEAN.replace("31 Desember", "30 Desember")),  # another end date
        scanned("Nama : Budi Santoso", CLEAN.replace("1 Januari", "2 Januari")),  # contradicts Tesseract's start
        scanned("Nama : Budi Santoso", CLEAN, "Perjanjian ini berlaku mulai tanggal 1 Juli 2026 sampai dengan 31 Juli 2026."),
        scanned("Nama : Budi Santoso", *BROKEN),  # misses it too
        None,  # the read failed
    ],
)
def test_a_second_reading_that_does_not_complete_it_cleanly_changes_nothing(again):
    level, result, runner = period_check(scanned("Nama : Budi Santoso", *BROKEN), again)
    assert runner.calls == [1] and level is Level.YELLOW
    assert result.tanggal_selesai is None and "periode_dari" not in result.log.validasi_nama_file
    assert "period_not_in_source" in result.log.alasan


@pytest.mark.parametrize(
    "first",
    [
        scanned("Nama : Budi Santoso", CLEAN),  # Tesseract read both dates
        scanned("Nama : Budi Santoso", CLEAN.replace("1 Januari", "2 Januari")),  # another period: not a missing date
        scanned("Nama : Budi Santoso", BROKEN[0], "PA ea", "Selesai pada 31 Desember 2026."),  # printed: the lookup decides
        scanned("Nama : Budi Santoso", *BROKEN, source="text_layer"),  # no scanned page to read again
    ],
)
def test_a_period_that_is_not_missing_a_date_is_not_read_again(first):
    _, result, runner = period_check(first, scanned("Nama : Budi Santoso", CLEAN))
    assert runner.calls == [] and result.log.periode_pembaca_kedua is None
    assert "periode_dari" not in result.log.validasi_nama_file


def test_the_period_and_the_nik_share_one_second_read():
    from contract_extractor.extractor_method.pattern.filename import parse_filename

    first = scanned("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}", *BROKEN)
    again = scanned("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}", CLEAN)
    result, parsed = FileResult(file=PERIOD_FILE), parse_filename(PERIOD_FILE)
    pages = pipeline._PagesRead({}, [first])
    pipeline._prepare_extraction(result, pages.p_id_pages, 100_000)
    level = pipeline._read_nik(result, pages.p_id_pages)
    runner = SecondRunner(again)
    pipeline._second_period(b"", result, parsed, pages, runner)
    assert pipeline._second_read(b"", result, pages, runner, level) is Level.GREEN
    assert runner.calls == [1] and result.log.komponen_halaman == {1: ["rapidocr@300dpi:pembaca_kedua"]}
