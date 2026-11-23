"""The 'claude_page' mode: the identity page goes to Claude as an image with the NIK blacked out, for the name, period,
title and employer; the NIK and the signature stay with the local reading. Also the RapidOCR-first engine order."""

import base64
import io
import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import anthropic
import cv2
import numpy as np
import pytest
from helpers import EMPLOYEE_NIK, IDENTITY_LINES, PERIOD_LINES, llm_answer, make_pdf, signed_contract_pdf

from contract_extractor import pipeline

from contract_extractor.common.config import ExtractorSettings, OcrEngine, OcrSettings, SecondReaderSettings, Settings
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import (
    Decision,
    NikStatus,
    PositionSource,
    Reader,
    SignatureStatus,
    TokenUsage,
    Word,
)
from contract_extractor.document.ocr import engine
from contract_extractor.document.pdf.reader import read_document
from contract_extractor.extractor_method.llm.base import (
    DateAnswer,
    ExtractionResult,
    ExtractorError,
    MonthsAnswer,
    TextAnswer,
)
from contract_extractor.extractor_method.llm.claude import ClaudeExtractor
from contract_extractor.extractor_method.llm.claude_code import ClaudeCodeExtractor
from contract_extractor.extractor_method.llm.gemini import GeminiVertexExtractor
from contract_extractor.extractor_method.llm.prompt import PAGE_PROMPT, PAGE_REQUEST, PAGE_SCHEMA
from contract_extractor.extractor_method.pattern.nik import nik_spot
from contract_extractor.identity_page import PAGE_MAX_SIDE, long_digit_boxes, masked_jpegs
from contract_extractor.pipeline import (
    CALL_IDENTITY_PAGE,
    FLAG_AUTO_DISABLED,
    REASON_EXTRACTOR_DISABLED,
    REASON_IDENTITY_TEXT,
    REASON_IDENTITY_IMAGE,
    REASON_IDENTITY_IMAGE_NOT_SENT,
    process_bytes,
)
from contract_extractor.worker import ERROR_BAD_HEADER, serve

FILENAME = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"
VENDOR = "PT Contoh Sejahtera"
USED = TokenUsage(masuk=12, cache_tulis=4000, keluar=900)
WEBSITE = Settings(extractor=ExtractorSettings(enabled=True, assist=True))


def page_answer(
    *,
    name: str | None = "Budi Santoso",
    start: date | None = date(2026, 1, 1),
    end: date | None = date(2026, 12, 31),
    title: str | None = "Staf Gudang",
    vendor: str | None = VENDOR,
    conf: float = 0.95,
) -> ExtractionResult:
    return ExtractionResult(
        nama_karyawan=TextAnswer(nilai=name, confidence=conf if name else 0.0),
        posisi=TextAnswer(nilai=title, confidence=conf if title else 0.0),
        tanggal_mulai=DateAnswer(nilai=start, confidence=conf if start else 0.0),
        tanggal_selesai=DateAnswer(nilai=end, confidence=conf if end else 0.0),
        durasi_bulan=MonthsAnswer(nilai=12, confidence=conf),
        vendor=TextAnswer(nilai=vendor, confidence=conf if vendor else 0.0),
        usage=USED,
    )


class PageReader:
    """Records what it was sent. The text call and the crops answer like the local reading, so a test sees which
    calls were made without them deciding anything."""

    name = "fake-llm"

    def __init__(
        self,
        answer: ExtractionResult | None = None,
        error: ExtractorError | None = None,
        text_answer: ExtractionResult | None = None,
    ):
        self.answer, self.error, self.text_answer = answer or page_answer(), error, text_answer
        self.pages: list[list[bytes]] = []
        self.texts: list[str] = []
        self.crops: list[str] = []

    def read_page(self, jpegs):
        self.pages.append(jpegs)
        if self.error:
            raise self.error
        return self.answer

    def extract(self, masked_text):
        self.texts.append(masked_text)
        if self.text_answer is None:
            raise ExtractorError("not_in_this_test")
        return self.text_answer

    def read_image(self, task, png):
        self.crops.append(task.value)
        raise ExtractorError("not_in_this_test")


@pytest.fixture
def contract(tmp_path) -> bytes:
    return signed_contract_pdf(tmp_path / FILENAME, "annotation").read_bytes()  # five pages, signature on the last


@pytest.fixture
def as_scan(monkeypatch):
    """The synthetic contract is a native PDF; the image tests treat its identity page as a scanned one."""
    monkeypatch.setattr(pipeline, "_text_layer_identity", lambda pages: False)


def test_a_native_identity_page_goes_as_masked_text_not_an_image(contract):
    reader = PageReader(text_answer=llm_answer())
    result, reader = _read(contract, reader)
    assert reader.pages == [] and len(reader.texts) == 1  # the text call, never an image
    assert EMPLOYEE_NIK not in reader.texts[0]  # masked as in the OCR mode
    assert result.log.pembaca is Reader.LLM and REASON_IDENTITY_TEXT in result.log.alasan
    assert (result.nama_karyawan, result.tanggal_selesai) == ("Budi Santoso", date(2026, 12, 31))


def _read(data: bytes, reader: PageReader | None = None, filename: str = FILENAME, settings: Settings = WEBSITE):
    reader = reader or PageReader()
    return process_bytes(data, filename, settings, None, reader, VENDOR, page_image=True), reader


def _decode(jpeg: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert image is not None, "not an image"
    return image


def test_claude_reads_name_period_and_title_from_the_identity_page_and_the_rest_stays_local(contract, as_scan):
    result, reader = _read(contract)

    [[jpeg]] = reader.pages  # one call, with the identity page only
    assert jpeg[:2] == b"\xff\xd8" and max(_decode(jpeg).shape) <= PAGE_MAX_SIDE
    assert (reader.texts, reader.crops) == ([], [])  # the text call is replaced, and the local NIK and ink were sure
    assert result.log.komponen_halaman[1][-1] == f"fake-llm:{CALL_IDENTITY_PAGE}"
    assert (result.nama_karyawan, result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan) == (
        "Budi Santoso",
        date(2026, 1, 1),
        date(2026, 12, 31),
        12,
    )
    assert (result.posisi, result.log.posisi.sumber, result.confidence["posisi"]) == ("Staf Gudang", PositionSource.LLM, 0.95)
    assert result.log.pembaca is Reader.LLM
    # the NIK, the signature and the vendor lookup are the local reading's
    assert (result.nik, result.log.nik.status, result.status_ttd) == (EMPLOYEE_NIK, NikStatus.FOUND, SignatureStatus.SIGNED)
    assert (result.log.vendor_dari, result.log.validasi_vendor, result.vendor_dokumen) == ("teks", "cocok", VENDOR)
    assert (result.log.panggilan_llm, result.log.token_llm) == ([CALL_IDENTITY_PAGE], USED)
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}
    assert result.log.alasan == [REASON_IDENTITY_IMAGE]
    assert (result.keputusan, result.flags) == (Decision.REVIEW, [FLAG_AUTO_DISABLED])  # every check is green


def test_the_image_sent_has_the_nik_painted_black(contract, as_scan):
    _, reader = _read(contract)
    [[jpeg]] = reader.pages
    image = _decode(jpeg)
    page = read_document(contract, 200).texts[0]
    x0, top, x1, bottom = nik_spot([page]).box
    h, w = image.shape
    nik_area = image[int(top * h) + 2 : int(bottom * h) - 2, int(x0 * w) + 2 : int(x1 * w) - 2]
    assert nik_area.size and nik_area.max() < 40  # black, give or take JPEG noise
    assert image.mean() > 200  # the rest of the page is still there to read


def test_a_page_whose_nik_cannot_be_located_is_never_sent_and_the_text_is_read_instead(tmp_path, as_scan):
    lines = [line for line in IDENTITY_LINES if not line.startswith("NIK")] + PERIOD_LINES
    pdf = make_pdf(tmp_path / FILENAME, [("text", "\n".join(lines))]).read_bytes()
    result, reader = _read(pdf)
    assert reader.pages == []
    assert REASON_IDENTITY_IMAGE_NOT_SENT in result.log.alasan
    assert CALL_IDENTITY_PAGE not in result.log.panggilan_llm and result.log.pembaca is not Reader.LLM


# --- the worker -------------------------------------------------------------------------------------------------


def _serve(*frames: bytes) -> list[dict]:
    out = io.BytesIO()
    serve(io.BytesIO(b"".join(frames)), out, Settings(), None, None)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _frame(request_id: int, data: bytes, **extra) -> bytes:
    header = {"id": request_id, "filename": FILENAME, "size": len(data), **extra}
    return json.dumps(header).encode() + b"\n" + data


@pytest.mark.parametrize("mode", ["ocr", "claude_page"])
def test_the_worker_accepts_both_modes(contract, mode):
    messages = _serve(_frame(1, contract, mode=mode))
    assert messages[1]["ok"] is True


@pytest.mark.parametrize("mode", ["full_page", "photo", "", 1, None])
def test_the_old_whole_page_mode_and_unknown_modes_are_a_bad_header(contract, mode):
    messages = _serve(_frame(1, contract, mode=mode))
    assert messages[1] == {"id": None, "ok": False, "error": ERROR_BAD_HEADER}


# --- the engine order -------------------------------------------------------------------------------------------


@pytest.fixture
def engines(monkeypatch):
    built = []

    def fake(kind):
        def build(settings):
            built.append(kind)
            return SimpleNamespace(name=kind)

        return build

    monkeypatch.setattr(engine, "_tesseract", fake("tesseract"))
    monkeypatch.setattr(engine, "_rapidocr", fake("rapidocr"))
    return built


@pytest.mark.parametrize(
    ("primary", "second_reader", "names", "built"),
    [
        (OcrEngine.TESSERACT, False, ("tesseract", None), ["tesseract"]),
        (OcrEngine.TESSERACT, True, ("tesseract", "rapidocr"), ["tesseract", "rapidocr"]),
        (OcrEngine.RAPIDOCR, False, ("rapidocr", None), ["rapidocr"]),
        (OcrEngine.RAPIDOCR, True, ("rapidocr", "tesseract"), ["rapidocr", "tesseract"]),
    ],
)
def test_the_primary_engine_reads_and_the_other_one_is_the_second_reader(engines, primary, second_reader, names, built):
    settings = Settings(ocr=OcrSettings(primary=primary, second_reader=SecondReaderSettings(enabled=second_reader)))
    runner = engine.build_runner(settings)
    assert (runner.primary.name, runner.second.name if runner.second else None) == names
    assert engines == built  # an engine that is not used is never started


# --- the providers ----------------------------------------------------------------------------------------------

PAGE_1, PAGE_2 = b"\xff\xd8identity page", b"\xff\xd8second identity page"
ANSWER = {
    "nama_karyawan": {"nilai": "Budi Santoso", "confidence": 0.95},
    "posisi": {"nilai": "Staf Gudang", "confidence": 0.9},
    "tanggal_mulai": {"nilai": "2026-01-01", "confidence": 0.95},
    "tanggal_selesai": {"nilai": "2026-12-31", "confidence": 0.95},
    "durasi_bulan": {"nilai": 12, "confidence": 0.9},
    "vendor": {"nilai": VENDOR, "confidence": 0.9},
}


class FakeAnthropic:
    def __init__(self, text: str = json.dumps(ANSWER)):
        self.requests: list[dict] = []
        self._text = text
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    @contextmanager
    def _stream(self, **request):
        self.requests.append(request)
        usage = SimpleNamespace(input_tokens=12, cache_creation_input_tokens=4000, output_tokens=900)
        content = [SimpleNamespace(type="text", text=self._text)]
        yield SimpleNamespace(get_final_message=lambda: SimpleNamespace(stop_reason="end_turn", content=content, usage=usage))


def _jpeg(block: dict) -> bytes:
    assert block["type"] == "image" and block["source"]["media_type"] == "image/jpeg"
    return base64.b64decode(block["source"]["data"])


def test_the_page_schema_is_in_the_subset_the_claude_api_accepts():
    assert anthropic.transform_schema(PAGE_SCHEMA) == PAGE_SCHEMA


def test_claude_reads_the_identity_pages_in_one_call():
    client = FakeAnthropic()
    extractor = ClaudeExtractor(client)
    answer = extractor.read_page([PAGE_1, PAGE_2])

    assert (answer.nama_karyawan.nilai, answer.tanggal_selesai.nilai, answer.posisi.nilai) == (
        "Budi Santoso",
        date(2026, 12, 31),
        "Staf Gudang",
    )
    assert answer.usage == USED
    [request] = client.requests
    [message] = request["messages"]
    first, second, ask = message["content"]
    assert (_jpeg(first), _jpeg(second), ask["text"]) == (PAGE_1, PAGE_2, PAGE_REQUEST)
    assert request["system"] == [{"type": "text", "text": PAGE_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert request["output_config"] == {"effort": extractor.effort, "format": {"type": "json_schema", "schema": PAGE_SCHEMA}}


def test_gemini_reads_no_page_image():
    with pytest.raises(ExtractorError) as caught:
        GeminiVertexExtractor(SimpleNamespace(), "gemini-x").read_page([PAGE_1])
    assert caught.value.kind == "image_unsupported"
