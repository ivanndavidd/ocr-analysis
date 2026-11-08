"""Mode tanpa OCR: halaman pertama dan terakhir dikirim utuh ke Claude (keputusan owner 2026-11-04, untuk uji).

Halaman pertama dianggap halaman identitas dan halaman terakhir halaman tanda tangan, tanpa page selection. Kedua
halaman dikirim sebagai gambar apa adanya, TANPA masking: nama, NIK, alamat, tanggal lahir, dan isi lain di halaman
itu ikut keluar. Owner dan mentornya belum mendapat keputusan apakah ini boleh; mode ini cadangan untuk diuji, dan
mode OCR (`pipeline.py`) tetap alur utama. Tesseract tidak dipakai sama sekali.

Semua field datang dari satu panggilan Claude; yang tetap dicek di laptop: NIK dengan tanggal lahir dan jenis kelamin
yang tercetak (dibaca Claude bersama NIK-nya), nama dan periode dengan nama file, perusahaan pemberi kerja dengan
folder vendor. Hasilnya `FileResult` yang sama dengan mode OCR, jadi website dan Excel membacanya dengan cara sama.
"""

from __future__ import annotations

import logging

import cv2

from contract_extractor.common.config import Settings
from contract_extractor.common.dates import months_between
from contract_extractor.common.models import (
    Decision,
    FileResult,
    Level,
    LlmPeriod,
    NikCheck,
    NikLog,
    NikStatus,
    PositionLog,
    PositionSource,
    PositionStatus,
    Reader,
    SignatureLog,
    SignatureStatus,
)
from contract_extractor.document.pdf.reader import InputRejected, read_document, validate_pdf_bytes
from contract_extractor.document.pdf.render import PageRenderer
from contract_extractor.extractor_method.llm.base import Extractor, ExtractorError, FullPageAnswer
from contract_extractor.extractor_method.llm.verification import SURE
from contract_extractor.extractor_method.pattern.filename import Match, ParsedFilename, compare_period, parse_filename
from contract_extractor.extractor_method.pattern.nik import check_printed
from contract_extractor.extractor_method.pattern.vendor import vendor_in_text
from contract_extractor.pipeline import (
    COMPONENT_NIK,
    COMPONENT_VENDOR,
    FLAG_AUTO_DISABLED,
    FLAG_INPUT_REJECTED,
    FLAG_PROCESSING_ERROR,
    LLM_SIGNED_CONF,
    LLM_UNSIGNED_CONF,
    REASON_EXTRACTOR_DISABLED,
    REASON_EXTRACTOR_ERROR,
    REASON_NIK_BIRTH_DATE_MISMATCH,
    REASON_NIK_GENDER_MISMATCH,
    REASON_NIK_INVALID,
    REASON_NIK_NOT_FOUND,
    REASON_SIGNATURE_UNCERTAIN,
    REASON_VENDOR_MISMATCH,
    REASON_VENDOR_NOT_IN_SOURCE,
    _add_reason,
    _count_call,
    _filename_level,
    file_id,
)
from contract_extractor.scoring import COMPONENT_FILENAME, decide

log = logging.getLogger(__name__)

MODE_OCR, MODE_FULL_PAGE = "ocr", "full_page"
REASON_FULL_PAGES = "read_full_pages"  # every value of this file came from the whole first and last page
CALL_PAGES = "halaman"
# Rendered at 200 dpi and sent at most 2000 px on the long side (about 170 dpi for A4), as JPEG: big enough to read a
# NIK digit by digit, small enough for one request. A model that reads fewer pixels scales the image down itself.
PAGE_DPI = 200
PAGE_MAX_SIDE = 2000
PAGE_JPEG_QUALITY = 85
SAME_NIK_CONF = 0.8  # a NIK both printed checks confirm is still coloured when the model was unsure of its digits
_GENDER = {"laki-laki": False, "perempuan": True}


def page_jpeg(renderer: PageRenderer, number: int) -> bytes:
    """One page in colour (a meterai and a blue pen keep their colours), as JPEG, at most PAGE_MAX_SIDE."""
    rgb = renderer.render_rgb(number, PAGE_DPI)
    height, width = rgb.shape[:2]
    scale = min(1.0, PAGE_MAX_SIDE / max(height, width))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, PAGE_JPEG_QUALITY])
    if not ok:
        raise ValueError("page could not be encoded")
    return encoded.tobytes()


def process_full_pages(
    data: bytes, filename: str, settings: Settings, extractor: Extractor | None, vendor: str | None = None
) -> FileResult:
    """The no-OCR mode for a PDF in memory; like `process_bytes`, it never raises and never writes the PDF to disk."""
    result = FileResult(file=filename, vendor=" ".join((vendor or "").split()) or None)
    try:
        parsed = parse_filename(filename)
        result.entitas = parsed.entity
        result.flags.extend(parsed.flags)
        validate_pdf_bytes(data, filename, settings.input.max_file_mb * 1024 * 1024)
        doc = read_document(data, settings.input.max_pages, economy_mode=True)  # page count and kinds, no middle pages
        result.log.n_halaman = doc.n_pages
        result.log.jenis_halaman = doc.kinds
        _add_reason(result, REASON_FULL_PAGES)
        result.status_ttd = SignatureStatus.UNCERTAIN
        if extractor is None:
            _add_reason(result, REASON_EXTRACTOR_DISABLED)
            return result
        last = doc.n_pages
        result.log.p_id, result.log.p_ttd = [1], last
        with PageRenderer(data) as renderer:
            first_page = page_jpeg(renderer, 1)
            last_page = page_jpeg(renderer, last) if last > 1 else None
        for number in sorted({1, last}):
            result.log.komponen_halaman.setdefault(number, []).append(f"{extractor.name}:halaman_utuh")
        try:
            answer = extractor.read_pages(first_page, last_page)
        except ExtractorError as exc:
            _count_call(result, extractor, CALL_PAGES, exc.usage)
            _add_reason(result, f"{REASON_EXTRACTOR_ERROR}:{exc.kind}")
            return result
        _count_call(result, extractor, CALL_PAGES, answer.usage)

        levels = _use_answer(result, answer, parsed)
        result.keputusan, extra_flags = decide(levels)
        result.flags.extend(extra_flags)
        if result.keputusan is Decision.AUTO and not settings.decision.allow_auto:
            result.keputusan = Decision.REVIEW
            result.flags.append(FLAG_AUTO_DISABLED)
    except InputRejected as exc:
        result.flags.append(FLAG_INPUT_REJECTED)
        result.log.alasan.append(exc.reason)
        log.warning("file %s rejected: %s", file_id(filename), exc.reason)
    except Exception as exc:  # isolate every per-file failure; record the type only, never the message
        result.flags.append(FLAG_PROCESSING_ERROR)
        result.log.alasan.append(f"error:{type(exc).__name__}")
        log.error("file %s failed: %s", file_id(filename), type(exc).__name__)
    return result


def _use_answer(result: FileResult, answer: FullPageAnswer, parsed: ParsedFilename) -> dict[str, Level]:
    """The model's reading becomes the file's, with the local checks that need no OCR."""
    result.log.pembaca = Reader.LLM
    levels = {"extraction": _name_and_period(result, answer, parsed)}
    if result.nama_karyawan:
        levels[COMPONENT_FILENAME] = _filename_level(result, parsed)
    levels[COMPONENT_NIK] = _nik(result, answer)
    _position(result, answer)
    vendor_level = _vendor(result, answer)
    if vendor_level is not None:
        levels[COMPONENT_VENDOR] = vendor_level
    levels["signature"] = _signature(result, answer)
    return levels


def _name_and_period(result: FileResult, answer: FullPageAnswer, parsed: ParsedFilename) -> Level:
    """Green when the name and both dates are read surely and the filename names the same period."""
    name, start, end = answer.nama_karyawan.nilai, answer.tanggal_mulai.nilai, answer.tanggal_selesai.nilai
    if start and end and end <= start:
        start = end = None  # not a period; a person reads it from the page
    written = answer.durasi_bulan.nilai
    result.nama_karyawan = name
    result.tanggal_mulai, result.tanggal_selesai = start, end
    result.durasi_bulan = written or (months_between(start, end) if start and end else None)
    result.log.periode_llm = LlmPeriod(tanggal_mulai=start, tanggal_selesai=end, durasi_bulan=written)
    result.confidence["nama"] = round(answer.nama_karyawan.confidence, 3) if name else 0.0
    sure_of_dates = min(answer.tanggal_mulai.confidence, answer.tanggal_selesai.confidence)
    result.confidence["periode"] = round(sure_of_dates, 3) if start and end else 0.0
    if not (name and start and end):
        return Level.YELLOW
    same_period = compare_period(parsed, start, end, written) is Match.COCOK
    sure = min(result.confidence["nama"], result.confidence["periode"]) >= SURE
    return Level.GREEN if sure and same_period else Level.YELLOW


def _nik(result: FileResult, answer: FullPageAnswer) -> Level:
    """Red when the printed birth date or gender contradicts the NIK; green only when both confirm it."""
    nik = answer.nik.nilai
    if not nik:
        result.log.nik = NikLog(status=NikStatus.MISSING)
        _add_reason(result, REASON_NIK_NOT_FOUND)
        return Level.YELLOW
    checked = check_printed(nik, answer.tanggal_lahir.nilai, _GENDER.get(answer.jenis_kelamin.nilai or ""))
    result.log.nik = checked
    if checked.status is not NikStatus.FOUND:
        _add_reason(result, REASON_NIK_INVALID)
        return Level.YELLOW
    result.nik = nik
    result.confidence["nik"] = round(answer.nik.confidence, 3)
    if checked.tanggal_lahir is NikCheck.MISMATCH:
        _add_reason(result, REASON_NIK_BIRTH_DATE_MISMATCH)
    if checked.jenis_kelamin is NikCheck.MISMATCH:
        _add_reason(result, REASON_NIK_GENDER_MISMATCH)
    if NikCheck.MISMATCH in (checked.tanggal_lahir, checked.jenis_kelamin):
        return Level.RED
    both = checked.tanggal_lahir is NikCheck.MATCH and checked.jenis_kelamin is NikCheck.MATCH
    return Level.GREEN if both and answer.nik.confidence >= SAME_NIK_CONF else Level.YELLOW


def _position(result: FileResult, answer: FullPageAnswer) -> None:
    title = answer.posisi.nilai
    if not title:
        result.log.posisi = PositionLog()
        return
    result.posisi = title
    result.log.posisi = PositionLog(status=PositionStatus.FOUND, sumber=PositionSource.LLM)
    result.confidence["posisi"] = round(answer.posisi.confidence, 3)


def _vendor(result: FileResult, answer: FullPageAnswer) -> Level | None:
    """The vendor folder against the employer company the model read; None when there is no folder to check."""
    result.vendor_dokumen = answer.vendor.nilai
    if not result.vendor:
        return None
    result.log.vendor_dari = "llm"
    same = bool(result.vendor_dokumen) and vendor_in_text(result.vendor, result.vendor_dokumen or "")
    result.log.validasi_vendor = (Match.COCOK if same else Match.TIDAK_COCOK).value
    if same:
        return Level.GREEN
    _add_reason(result, REASON_VENDOR_MISMATCH if result.vendor_dokumen else REASON_VENDOR_NOT_IN_SOURCE)
    return Level.YELLOW


def _signature(result: FileResult, answer: FullPageAnswer) -> Level:
    """The model alone decides, with the same cutoffs as for a crop in the OCR mode; below them it stays uncertain."""
    look = answer.tanda_tangan
    result.log.tanda_tangan = SignatureLog(sumber="llm", llm=look.status, llm_conf=round(look.confidence, 3))
    result.confidence["ttd"] = round(look.confidence, 3)
    if look.status == "signed" and look.confidence >= LLM_SIGNED_CONF:
        result.status_ttd = SignatureStatus.SIGNED
    elif look.status == "unsigned" and look.confidence >= LLM_UNSIGNED_CONF:
        result.status_ttd = SignatureStatus.UNSIGNED
    else:
        result.status_ttd = SignatureStatus.UNCERTAIN
        _add_reason(result, REASON_SIGNATURE_UNCERTAIN)
        return Level.YELLOW
    return Level.GREEN
