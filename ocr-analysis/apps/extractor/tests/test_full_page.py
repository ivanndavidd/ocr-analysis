"""The no-OCR mode: the first and last page go whole to the extractor, and only its reading is checked locally."""

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
from helpers import EMPLOYEE_NIK, make_pdf, signed_contract_pdf

from contract_extractor.common.config import Settings
from contract_extractor.common.models import (
    Decision,
    NikCheck,
    NikLog,
    NikStatus,
    Reader,
    SignatureStatus,
    TokenUsage,
)
from contract_extractor.extractor_method.llm.base import (
    DateAnswer,
    ExtractorError,
    FullPageAnswer,
    GenderAnswer,
    NikAnswer,
    SignatureAnswer,
    TextAnswer,
)
from contract_extractor.extractor_method.llm.claude import ClaudeExtractor
from contract_extractor.extractor_method.llm.claude_code import ClaudeCodeExtractor
from contract_extractor.extractor_method.llm.gemini import GeminiVertexExtractor
from contract_extractor.extractor_method.llm.prompt import (
    FIRST_PAGE_LABEL,
    FULL_PAGE_PROMPT,
    FULL_PAGE_REQUEST,
    FULL_PAGE_SCHEMA,
    LAST_PAGE_LABEL,
    ONE_PAGE_REQUEST,
)
from contract_extractor.full_page import PAGE_MAX_SIDE, REASON_FULL_PAGES, process_full_pages
from contract_extractor.pipeline import (
    FLAG_AUTO_DISABLED,
    FLAG_INPUT_REJECTED,
    REASON_EXTRACTOR_DISABLED,
    REASON_NIK_BIRTH_DATE_MISMATCH,
    REASON_NIK_INVALID,
    REASON_NIK_NOT_FOUND,
    REASON_SIGNATURE_UNCERTAIN,
    REASON_VENDOR_MISMATCH,
    REASON_VENDOR_NOT_IN_SOURCE,
)
from contract_extractor.worker import ERROR_BAD_HEADER, serve

FILENAME = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"
VENDOR = "PT Contoh Sejahtera"
USED = TokenUsage(masuk=12, cache_tulis=13000, keluar=2200)


def page_answer(
    *,
    name: str | None = "Budi Santoso",
    nik: str | None = EMPLOYEE_NIK,  # fictitious: born 1 January 1990, male
    nik_conf: float = 0.95,
    birth: date | None = date(1990, 1, 1),
    gender: str | None = "laki-laki",
    signature: tuple[str, float] = ("signed", 0.9),
    vendor: str | None = VENDOR,
    conf: float = 0.95,
) -> FullPageAnswer:
    return FullPageAnswer(
        nama_karyawan=TextAnswer(nilai=name, confidence=conf),
        nik=NikAnswer(nilai=nik, confidence=nik_conf),
        tanggal_lahir=DateAnswer(nilai=birth, confidence=conf),
        jenis_kelamin=GenderAnswer(nilai=gender, confidence=conf),
        posisi=TextAnswer(nilai="Staf Gudang", confidence=conf),
        tanggal_mulai=DateAnswer(nilai=date(2026, 1, 1), confidence=conf),
        tanggal_selesai=DateAnswer(nilai=date(2026, 12, 31), confidence=conf),
        vendor=TextAnswer(nilai=vendor, confidence=conf),
        tanda_tangan=SignatureAnswer(status=signature[0], confidence=signature[1]),
        usage=USED,
    )


class PageReader:
    """Records the two pages it was given; never reads text or crops, as the no-OCR mode sends neither."""

    name = "fake-llm"

    def __init__(self, answer: FullPageAnswer | None = None, error: ExtractorError | None = None):
        self.answer, self.error = answer or page_answer(), error
        self.pages: list[tuple[bytes, bytes | None]] = []

    def extract(self, masked_text):
        raise AssertionError("the no-OCR mode sends no text")

    def read_image(self, task, png):
        raise AssertionError("the no-OCR mode sends no crops")

    def read_pages(self, first, last):
        self.pages.append((first, last))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def contract(tmp_path) -> bytes:
    return signed_contract_pdf(tmp_path / FILENAME, "annotation").read_bytes()  # five pages, signature on the last


def _read(data: bytes, answer: FullPageAnswer | None = None, vendor: str | None = VENDOR, filename: str = FILENAME):
    reader = PageReader(answer)
    return process_full_pages(data, filename, Settings(), reader, vendor), reader


def _size(jpeg: bytes) -> tuple[int, int]:
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, "not an image"
    return image.shape[1], image.shape[0]


def test_the_first_and_last_page_go_whole_and_every_field_is_the_extractors(contract):
    result, reader = _read(contract)

    [(first, last)] = reader.pages
    assert first[:2] == last[:2] == b"\xff\xd8"  # JPEG
    assert max(_size(first)) <= PAGE_MAX_SIDE and max(_size(last)) <= PAGE_MAX_SIDE
    assert first != last
    assert (result.log.p_id, result.log.p_ttd) == ([1], 5)
    assert result.log.komponen_halaman == {1: ["fake-llm:halaman_utuh"], 5: ["fake-llm:halaman_utuh"]}
    assert result.log.ocr_conf == {}  # nothing was read with Tesseract

    assert (result.nama_karyawan, result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan) == (
        "Budi Santoso",
        date(2026, 1, 1),
        date(2026, 12, 31),
        12,
    )
    assert (result.nik, result.posisi, result.vendor_dokumen) == (EMPLOYEE_NIK, "Staf Gudang", VENDOR)
    assert result.log.nik == NikLog(status=NikStatus.FOUND, tanggal_lahir=NikCheck.MATCH, jenis_kelamin=NikCheck.MATCH)
    assert result.status_ttd is SignatureStatus.SIGNED
    assert result.log.pembaca is Reader.LLM
    assert (result.log.panggilan_llm, result.log.token_panggilan, result.log.token_llm) == (["halaman"], [USED], USED)
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}
    assert (result.log.vendor_dari, result.log.validasi_vendor) == ("llm", "cocok")
    assert result.log.alasan == [REASON_FULL_PAGES]
    # every check is green, so only the calibration gate keeps it from auto
    assert (result.keputusan, result.flags) == (Decision.REVIEW, [FLAG_AUTO_DISABLED])


def test_a_one_page_contract_sends_that_page_once(tmp_path):
    pdf = make_pdf(tmp_path / FILENAME, [("text", "PERJANJIAN KERJA WAKTU TERTENTU\nNama : Budi Santoso")]).read_bytes()
    result, reader = _read(pdf)
    [(first, last)] = reader.pages
    assert first[:2] == b"\xff\xd8" and last is None
    assert (result.log.p_id, result.log.p_ttd, result.log.komponen_halaman) == ([1], 1, {1: ["fake-llm:halaman_utuh"]})


@pytest.mark.parametrize(
    ("answer", "level_reason", "decision"),
    [
        # the printed birth date contradicts the NIK: red
        (page_answer(birth=date(1991, 1, 1)), REASON_NIK_BIRTH_DATE_MISMATCH, Decision.REVIEW_PRIORITY),
        # both printed checks agree, but the model was unsure of the digits
        (page_answer(nik_conf=0.6), None, Decision.REVIEW),
        # nothing printed to check the NIK against
        (page_answer(gender=None), None, Decision.REVIEW),
        (page_answer(nik=None), REASON_NIK_NOT_FOUND, Decision.REVIEW),
        (page_answer(nik="1234567890123456"), REASON_NIK_INVALID, Decision.REVIEW),  # no valid NIK
    ],
)
def test_the_nik_is_trusted_only_when_the_printed_birth_date_and_gender_confirm_it(contract, answer, level_reason, decision):
    result, _ = _read(contract, answer)
    assert result.keputusan is decision
    if level_reason:
        assert level_reason in result.log.alasan
    if level_reason in (REASON_NIK_NOT_FOUND, REASON_NIK_INVALID):
        assert result.nik is None


@pytest.mark.parametrize(
    ("look", "status"),
    [
        (("signed", 0.8), SignatureStatus.SIGNED),
        (("signed", 0.79), SignatureStatus.UNCERTAIN),
        (("unsigned", 0.85), SignatureStatus.UNSIGNED),
        (("unsigned", 0.84), SignatureStatus.UNCERTAIN),
        (("unclear", 0.99), SignatureStatus.UNCERTAIN),
    ],
)
def test_the_signature_is_decided_with_the_same_cutoffs_as_a_crop(contract, look, status):
    result, _ = _read(contract, page_answer(signature=look))
    assert result.status_ttd is status
    assert (REASON_SIGNATURE_UNCERTAIN in result.log.alasan) is (status is SignatureStatus.UNCERTAIN)
    assert result.log.tanda_tangan.sumber == "llm" and result.log.tanda_tangan.llm_conf == look[1]


def test_the_name_and_period_must_match_the_filename(contract):
    other = "Rina Wulandari - 1 Januari 2026 - 31 Desember 2026.pdf"
    result, _ = _read(contract, filename=other)
    assert result.log.validasi_nama_file["nama"] == "tidak_cocok"
    assert result.keputusan is Decision.REVIEW_PRIORITY  # another person than the filename names
    unsure, _ = _read(contract, page_answer(conf=0.5))
    assert unsure.keputusan is Decision.REVIEW and FLAG_AUTO_DISABLED not in unsure.flags


def test_the_vendor_folder_is_checked_against_the_employer_read(contract):
    other, _ = _read(contract, page_answer(vendor="PT Lain Sekali"))
    assert other.log.validasi_vendor == "tidak_cocok" and REASON_VENDOR_MISMATCH in other.log.alasan
    unread, _ = _read(contract, page_answer(vendor=None))
    assert REASON_VENDOR_NOT_IN_SOURCE in unread.log.alasan
    no_folder, _ = _read(contract, vendor=None)
    assert no_folder.log.validasi_vendor is None and no_folder.vendor_dokumen == VENDOR


def test_a_failed_call_reads_nothing_and_still_counts_its_tokens(contract):
    reader = PageReader(error=ExtractorError("timeout", USED))
    result = process_full_pages(contract, FILENAME, Settings(), reader, VENDOR)
    assert result.log.alasan == [REASON_FULL_PAGES, "extractor_error:timeout"]
    assert (result.log.panggilan_llm, result.log.token_llm) == (["halaman"], USED)
    assert (result.nama_karyawan, result.nik, result.status_ttd) == (None, None, SignatureStatus.UNCERTAIN)
    assert result.log.pembaca is not Reader.LLM


def test_without_an_extractor_nothing_is_rendered_or_read(contract):
    result = process_full_pages(contract, FILENAME, Settings(), None, VENDOR)
    assert result.log.alasan == [REASON_FULL_PAGES, REASON_EXTRACTOR_DISABLED]
    assert result.log.komponen_halaman == {} and result.status_ttd is SignatureStatus.UNCERTAIN


def test_a_file_that_is_not_a_pdf_is_rejected_before_anything_is_sent():
    reader = PageReader()
    result = process_full_pages(b"not a pdf at all", FILENAME, Settings(), reader)
    assert FLAG_INPUT_REJECTED in result.flags and reader.pages == []


# --- the worker -------------------------------------------------------------------------------------------------


def _serve(*frames: bytes) -> list[dict]:
    out = io.BytesIO()
    serve(io.BytesIO(b"".join(frames)), out, Settings(), None, None)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _frame(request_id: int, data: bytes, **extra) -> bytes:
    header = {"id": request_id, "filename": FILENAME, "size": len(data), **extra}
    return json.dumps(header).encode() + b"\n" + data


def test_the_worker_reads_in_the_mode_its_header_names(contract):
    messages = _serve(_frame(1, contract, mode="full_page"))
    assert messages[1]["ok"] is True
    assert messages[1]["result"]["log"]["alasan"] == [REASON_FULL_PAGES, REASON_EXTRACTOR_DISABLED]


@pytest.mark.parametrize("mode", ["photo", "", 1, None])
def test_an_unknown_mode_is_a_bad_header(contract, mode):
    messages = _serve(_frame(1, contract, mode=mode))
    assert messages[1] == {"id": None, "ok": False, "error": ERROR_BAD_HEADER}


# --- the providers ----------------------------------------------------------------------------------------------

FIRST, LAST = b"\xff\xd8first page", b"\xff\xd8last page"
ANSWER = {
    "nama_karyawan": {"nilai": "Budi Santoso", "confidence": 0.95},
    "nik": {"nilai": "3199 9901 0190 0001", "confidence": 0.9},
    "tanggal_lahir": {"nilai": "1990-01-01", "confidence": 0.9},
    "jenis_kelamin": {"nilai": "laki-laki", "confidence": 0.9},
    "posisi": {"nilai": "Staf Gudang", "confidence": 0.9},
    "tanggal_mulai": {"nilai": "2026-01-01", "confidence": 0.95},
    "tanggal_selesai": {"nilai": "2026-12-31", "confidence": 0.95},
    "durasi_bulan": {"nilai": 12, "confidence": 0.9},
    "vendor": {"nilai": VENDOR, "confidence": 0.9},
    "tanda_tangan": {"status": "signed", "confidence": 0.9},
}


class FakeAnthropic:
    def __init__(self, text: str = json.dumps(ANSWER)):
        self.requests: list[dict] = []
        self._text = text
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    @contextmanager
    def _stream(self, **request):
        self.requests.append(request)
        usage = SimpleNamespace(input_tokens=12, cache_creation_input_tokens=13000, output_tokens=2200)
        content = [SimpleNamespace(type="text", text=self._text)]
        yield SimpleNamespace(get_final_message=lambda: SimpleNamespace(stop_reason="end_turn", content=content, usage=usage))


def _jpeg(block: dict) -> bytes:
    assert block["type"] == "image" and block["source"]["media_type"] == "image/jpeg"
    return base64.b64decode(block["source"]["data"])


def test_full_page_schema_is_in_the_subset_the_claude_api_accepts():
    assert anthropic.transform_schema(FULL_PAGE_SCHEMA) == FULL_PAGE_SCHEMA


def test_claude_reads_both_pages_in_one_call_after_their_labels():
    client = FakeAnthropic()
    extractor = ClaudeExtractor(client)
    answer = extractor.read_pages(FIRST, LAST)

    assert (answer.nik.nilai, answer.jenis_kelamin.nilai, answer.tanda_tangan.status) == (EMPLOYEE_NIK, "laki-laki", "signed")
    assert answer.usage == TokenUsage(masuk=12, cache_tulis=13000, keluar=2200)
    [request] = client.requests
    [message] = request["messages"]
    first_label, first, last_label, last, ask = message["content"]
    assert (first_label["text"], last_label["text"], ask["text"]) == (FIRST_PAGE_LABEL, LAST_PAGE_LABEL, FULL_PAGE_REQUEST)
    assert (_jpeg(first), _jpeg(last)) == (FIRST, LAST)
    assert request["system"] == [{"type": "text", "text": FULL_PAGE_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert request["output_config"] == {"effort": extractor.effort, "format": {"type": "json_schema", "schema": FULL_PAGE_SCHEMA}}


def test_claude_reads_a_one_page_contract_with_its_own_request():
    client = FakeAnthropic()
    ClaudeExtractor(client).read_pages(FIRST, None)
    image, ask = client.requests[0]["messages"][0]["content"]
    assert _jpeg(image) == FIRST and ask["text"] == ONE_PAGE_REQUEST


def test_an_answer_outside_the_schema_is_an_error_that_keeps_its_tokens():
    with pytest.raises(ExtractorError) as caught:
        ClaudeExtractor(FakeAnthropic(text=json.dumps({**ANSWER, "jenis_kelamin": {"nilai": "pria"}}))).read_pages(FIRST, LAST)
    assert caught.value.kind == "invalid_output" and caught.value.usage is not None
    assert "pria" not in str(caught.value)


def test_claude_code_sends_the_pages_through_stdin_never_argv():
    result = {"type": "result", "subtype": "success", "is_error": False, "structured_output": ANSWER}
    calls: list[dict] = []

    def run(argv, stdin, cwd, timeout_s):
        prompt = Path(argv[argv.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")
        calls.append({"argv": argv, "stdin": stdin, "prompt": prompt})
        return 0, json.dumps({"type": "system"}) + "\n" + json.dumps(result) + "\n"

    extractor = ClaudeCodeExtractor(Path("claude.exe"), run=run)
    answer = extractor.read_pages(FIRST, LAST)

    assert answer.nik.nilai == EMPLOYEE_NIK
    [call] = calls
    argv = call["argv"]
    content = json.loads(call["stdin"])["message"]["content"]
    assert [part["type"] for part in content] == ["text", "image", "text", "image", "text"]
    assert (_jpeg(content[1]), _jpeg(content[3])) == (FIRST, LAST)
    assert content[1]["source"]["data"] not in " ".join(argv)
    assert call["prompt"] == FULL_PAGE_PROMPT
    assert (argv[argv.index("--input-format") + 1], argv[argv.index("--output-format") + 1]) == ("stream-json", "stream-json")
    assert argv[argv.index("--effort") + 1] == extractor.effort
    assert json.loads(argv[argv.index("--json-schema") + 1]) == FULL_PAGE_SCHEMA


def test_gemini_reads_no_pages():
    with pytest.raises(ExtractorError) as caught:
        GeminiVertexExtractor(SimpleNamespace(), "gemini-x").read_pages(FIRST, LAST)
    assert caught.value.kind == "image_unsupported"
