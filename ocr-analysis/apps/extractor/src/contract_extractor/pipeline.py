"""Orkestrasi per file. Satu file rusak tidak pernah menghentikan batch (FR-1)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path, PurePath

import cv2
import numpy as np
from rapidfuzz import fuzz

from contract_extractor.common.config import Settings
from contract_extractor.extractor_method.llm.base import ExtractionResult, Extractor, ExtractorError, ImageTask
from contract_extractor.common.dates import months_between
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import (
    Decision,
    FileResult,
    Level,
    LlmPeriod,
    NikCheck,
    NikLog,
    NikStatus,
    PageKind,
    PositionLog,
    PositionSource,
    PositionStatus,
    Reader,
    SignatureLog,
    SignatureStatus,
    TokenUsage,
)
from contract_extractor.document.ocr.engine import OcrRunner
from contract_extractor.document.ocr.preprocess import deskew, straighten
from contract_extractor.document.pdf.objects import Box, read_page_objects
from contract_extractor.document.pdf.reader import (
    DocumentRead,
    InputRejected,
    read_document,
    validate_pdf_bytes,
    validate_pdf_file,
)
from contract_extractor.document.pdf.render import PageRenderer
from contract_extractor.extractor_method.llm.verification import (
    REASON_NAME_NOT_IN_SOURCE,
    REASON_PERIOD_NOT_IN_SOURCE,
    VerifiedExtraction,
    extraction_level,
    verify_extraction,
)
from contract_extractor.extractor_method.pattern.filename import (
    FLAG_NONSTANDARD,
    FLAG_UNPARSED,
    Match,
    ParsedFilename,
    compare_name,
    compare_period,
    name_printed,
    normalize_name,
    parse_filename,
)
from contract_extractor.signature.ink import find_meterai
from contract_extractor.extractor_method.llm.masking import PAGE_SEPARATOR, MaskLeakError, mask_pages
from contract_extractor.extractor_method.pattern.nik import check_nik, find_nik, nik_spot
from contract_extractor.extractor_method.pattern.position import SAME_POSITION, find_position
from contract_extractor.document.pages.page_classify import vendor_note
from contract_extractor.document.pages.page_selection import select_pages
from contract_extractor.extractor_method.pattern.period_regex import dates_in_text, find_period
from contract_extractor.extractor_method.pattern.vendor import vendor_in_text
from contract_extractor.scoring import (
    COMPONENT_FILENAME,
    FLAG_FILENAME_MISMATCH,
    decide,
    filename_level,
    nik_level,
    ocr_level,
    signature_level,
)
from contract_extractor.signature.signature import (
    ANCHOR_NAME,
    detect_signature,
    employee_labels,
    employee_name_printed,
)

log = logging.getLogger(__name__)

# pdfminer logs raw content-stream tokens at DEBUG, which would copy contract text (names, NIK) into logs.
for _pdf_logger in ("pdfminer", "pdfplumber"):
    logging.getLogger(_pdf_logger).setLevel(logging.WARNING)

FLAG_INPUT_REJECTED = "input_rejected"
FLAG_PROCESSING_ERROR = "processing_error"
FLAG_AUTO_DISABLED = "auto_disabled"
FLAG_FORMAT_NONSTANDARD = "format_nonstandard"
COMPONENT_FORMAT = "format_halaman"
# No page scored high enough; one reason per missing page, so a reviewer knows which page to look for.
REASON_IDENTITY_PAGE_NOT_FOUND = "identity_page_not_found"
REASON_SIGNATURE_PAGE_NOT_FOUND = "signature_page_not_found"
REASON_SIGNATURE_PAGE_GUESSED = "signature_page_guessed"  # no page scored as one; the best candidate was checked
_PAGE_NOT_FOUND = (REASON_IDENTITY_PAGE_NOT_FOUND, REASON_SIGNATURE_PAGE_NOT_FOUND)
REASON_OCR_UNAVAILABLE = "ocr_unavailable"
REASON_OCR_LOW_CONFIDENCE = "ocr_low_confidence"
REASON_MASK_LEAK = "mask_leak"
REASON_EXTRACTOR_DISABLED = "extractor_disabled"
REASON_PERIOD_CONFLICT = "period_conflict"  # the document text gives several different periods
REASON_PAGE_SELECTION_LOW_MARGIN = "page_selection_low_margin"  # chosen, but other pages scored close
REASON_SIGNATURE_NOT_CLEAR = "signature_not_clear"  # anchored by a label only, or ink near the threshold
REASON_EXTRACTOR_ERROR = "extractor_error"
# The P_id OCR failed its quality check, so its text was not sent; the file is read the local way instead.
REASON_EXTRACTOR_NOT_SENT = "extractor_not_sent"
# The website's flow with Claude on: the local reading settled every field the text call reads, so nothing was sent.
REASON_EXTRACTOR_NOT_NEEDED = "extractor_not_needed"
REASON_SIGNATURE_PAGE_UNREADABLE = "signature_page_unreadable"
REASON_SIGNATURE_ANCHOR_NOT_FOUND = "signature_anchor_not_found"
REASON_SIGNATURE_UNCERTAIN = "signature_uncertain"
REASON_NIK_NOT_FOUND = "nik_not_found"
REASON_NIK_INVALID = "nik_invalid"
REASON_NIK_CONFLICT = "nik_conflict"
REASON_NIK_BIRTH_DATE_MISMATCH = "nik_birth_date_mismatch"
REASON_NIK_GENDER_MISMATCH = "nik_gender_mismatch"
REASON_NIK_FROM_OCR = "nik_from_ocr"
# More than 16 digits followed the label and one 16-digit window was picked: a person or a crop checks it. On the
# website run of 300 synthetic contracts (2026-11-04) one such pick had the first 3 digits wrong and passed both checks.
REASON_NIK_DIGITS_REPAIRED = "nik_digits_repaired"
REASON_NIK_FROM_LLM = "nik_from_llm"  # the local reading was unsure; the extractor's reading of a crop is used
REASON_NIK_LLM_DIFFERS = "nik_llm_differs"  # the extractor read other digits from the crop, not used
_LOCAL_NIK_REASONS = (
    REASON_NIK_NOT_FOUND,
    REASON_NIK_INVALID,
    REASON_NIK_BIRTH_DATE_MISMATCH,
    REASON_NIK_GENDER_MISMATCH,
    REASON_NIK_FROM_OCR,
    REASON_NIK_DIGITS_REPAIRED,
)
REASON_SIGNATURE_FROM_LLM = "signature_from_llm"  # the local check was unsure; the extractor's look at a crop decided
REASON_SIGNATURE_LLM_DIFFERS = "signature_llm_differs"  # the local check read unsigned; the extractor saw a signature
# A scan or photo the local check read signed; the extractor saw no signature in the crop. On the 800-contract website
# run (2026-11-04) the ink check read a black-and-white scan with only a meterai as signed, and nothing marked it.
REASON_SIGNATURE_LLM_SEES_NONE = "signature_llm_sees_none"
# A local signed or unsigned the ink check called not clear; the extractor's look at the crop saw the same, sure enough.
REASON_SIGNATURE_LLM_AGREES = "signature_llm_agrees"
COMPONENT_NIK = "nik"
COMPONENT_VENDOR = "vendor"
REASON_VENDOR_NOT_IN_SOURCE = "vendor_not_in_source"  # the vendor folder's name is not printed on the read pages
REASON_VENDOR_MISMATCH = "vendor_mismatch"  # the extractor read another employer company than the vendor folder
# `validasi_nama_file` key set when the filename's name was confirmed in the signature block, not on the identity page.
NAME_PAGE_KEY, NAME_PAGE_SIGNATURE = "nama_halaman", "tanda_tangan"
# Set when the second OCR's reading completed a period Tesseract's text left a date of unread.
PERIOD_FROM_KEY, PERIOD_FROM_SECOND = "periode_dari", "pembaca_kedua"
# The patterns' own reasons for a yellow file; the fallback extractor's verification decides them afresh.
_PATTERN_REASONS = (REASON_NAME_NOT_IN_SOURCE, REASON_PERIOD_NOT_IN_SOURCE)
_NIK_STATUS_REASONS = {
    NikStatus.MISSING: REASON_NIK_NOT_FOUND,
    NikStatus.INVALID: REASON_NIK_INVALID,
    NikStatus.CONFLICT: REASON_NIK_CONFLICT,
}

SIGNATURE_DPI = 300  # native pages; scans reuse the OCR read resolution so word coordinates line up
# How sure the extractor must be about a signature crop to decide it. Signed was set higher at first, since a wrong
# "signed" can let an unsigned contract pass. On 300 synthetic contracts through the website (2026-11-04, Haiku 5.5)
# Claude said signed 27 times, all signed, and never for any of about 55 unsigned crops; 9 of the 27 came at 0.80 to
# 0.88 and stayed uncertain under a 0.9 bar. In the rerun on the regenerated 300 Claude said unsigned 55 times: the
# one wrong answer (a signed crop half under a black meterai) came at 0.80, every right one at 0.87 or more.
LLM_SIGNED_CONF = 0.8
LLM_UNSIGNED_CONF = 0.85
# How sure the extractor must be to settle a local signed or unsigned that the ink check called not clear (ink near
# its threshold): the same answer this surely makes it sure. On the 973 synthetic scans and photos of both datasets
# (benchmark, 2026-11-13, Haiku 5.5) Claude agreed at 0.85 or more with 371 such signatures and 370 were right; the
# wrong one was a signed contract both read as unsigned. Below 0.85 it agreed 6 times, all right, too few to judge.
LLM_AGREE_CONF = 0.85
CROP_MAX_SIDE = 1568  # pixels; an image costs about width x height / 750 tokens, and this is plenty to read ink
# A title read below this confidence is coloured on the website (LOW_CONFIDENCE in apps/backend jobs/fields.ts), so
# the second reader reads it again.
POSITION_SURE = 0.8
_LEVEL_ORDER = (Level.GREEN, Level.YELLOW, Level.RED)

# Unclassified pages carry content in a non-standard form (a pasted photo, outlined text): read them rather than skip.
_NEEDS_OCR = (PageKind.SCAN, PageKind.HYBRID, PageKind.UNCLASSIFIED)


@dataclass
class _PagesRead:
    levels: dict[str, Level]
    p_id_pages: list[PageText] = field(default_factory=list)  # full-resolution text, in page order
    p_id_trusted: bool = False  # every P_id page came from a text layer or from OCR that passed quality
    p_ttd_page: PageText | None = None  # full-resolution text of the signature page
    turns: dict[int, int] = field(default_factory=dict)  # page -> clockwise turn that made a turned photo upright
    # The identity pages as the second OCR reads them, read at most once per file (see `_second_pages`).
    second: list[PageText] | None = None
    second_tried: bool = False


def file_id(filename: str) -> str:
    """Filenames carry employee names, so logs refer to files by a short hash only."""
    return hashlib.sha256(PurePath(filename).name.encode("utf-8")).hexdigest()[:12]


def _add_reason(result: FileResult, reason: str) -> None:
    if reason not in result.log.alasan:
        result.log.alasan.append(reason)


def _select_and_read(
    data: bytes, doc: DocumentRead, result: FileResult, employee_name: str, quality_name: str, ocr: OcrRunner | None
) -> _PagesRead:
    """Tahap 1 and 2. The first page is the identity page and the last the signature page (owner 2026-11-10), so only
    those two are read, in full, and then scored to check that each looks like one; the middle pages are never read.
    Choosing the pages from a rough read of the first 3 and last 5 made a scan of 7 pages or more take about 27-29 s
    against 16 s for one of 2 pages (800 synthetic contracts, 2026-11-10)."""
    components = result.log.komponen_halaman
    for page in doc.texts:
        components.setdefault(page.number, []).append("text_layer")

    numbers = doc.read_pages
    to_ocr = [n for n in numbers if doc.kinds.get(n) in _NEEDS_OCR]
    if to_ocr and ocr is None:
        _add_reason(result, REASON_OCR_UNAVAILABLE)
    renderer = PageRenderer(data) if to_ocr and ocr is not None else None
    try:
        native = {page.number: page for page in doc.texts}
        read_pages: dict[int, PageText] = {}
        passed: set[int] = set()
        engines: dict[int, str] = {}
        confidences: dict[int, float] = {}

        def read(number: int) -> None:
            passed.discard(number)
            read_pages.pop(number, None)
            if doc.kinds[number] is PageKind.NATIVE:
                engines[number], confidences[number] = "text_layer", 1.0
                read_pages[number] = native[number]
                passed.add(number)
            elif renderer is None or ocr is None:
                engines[number], confidences[number] = "none", 0.0
            else:
                # Only the identity page is reread with table borders erased when it fails the quality check. The
                # signature check was calibrated on the signature page's plain read; erasing there turned one unsigned
                # synthetic contract signed.
                identity = number == numbers[0]
                erase = identity and number != numbers[-1]
                outcome = ocr.read(renderer, number, quality_name if identity else "", erase)
                engines[number] = outcome.engine or "none"
                confidences[number] = outcome.mean_conf
                components.setdefault(number, []).append(f"{engines[number]}@{ocr.read_dpi}dpi")
                result.log.ocr_conf[number] = round(outcome.mean_conf, 3)
                if outcome.page is not None:
                    read_pages[number] = outcome.page
                if outcome.passed:
                    passed.add(number)

        for number in numbers:
            read(number)
        selection = select_pages(list(read_pages.values()), doc.n_pages, employee_name, doc.blank)
        if selection.ambiguous and renderer is not None and ocr is not None:
            # A photo taken sideways or upside down reads as noise; only a page that failed its role is turned.
            failed = ([numbers[0]] if not selection.p_id else []) + ([numbers[-1]] if selection.p_ttd is None else [])
            turned = [
                n for n in dict.fromkeys(failed) if n in to_ocr and _turn_upright(renderer, ocr, n, read_pages, result)
            ]
            for number in turned:
                read(number)
            if turned:
                selection = select_pages(list(read_pages.values()), doc.n_pages, employee_name, doc.blank)
        if renderer is not None and any(n not in passed for n in to_ocr):
            _add_reason(result, REASON_OCR_LOW_CONFIDENCE)
        result.log.p_id = selection.p_id
        result.log.p_ttd = selection.p_ttd
        result.log.halaman_sensitif = selection.sensitive_pages
        result.log.skor_halaman = {n: s.as_dict() for n, s in sorted(selection.scores.items())}
        if not selection.p_id:
            # Without the identity page nothing is read and no cloud stage runs; the file goes straight to review.
            _add_reason(result, REASON_IDENTITY_PAGE_NOT_FOUND)
            if selection.p_ttd is None:
                _add_reason(result, REASON_SIGNATURE_PAGE_NOT_FOUND)
            return _PagesRead({"page_selection": selection.level, "ocr_p_id": Level.RED})
        p_ttd = selection.p_ttd
        if p_ttd is None:
            # The identity page is still read: stopping here lost every value of 3 synthetic photos whose signature page
            # scored low. The last page is checked as the signature page all the same; the red page selection keeps
            # the file in review, and the website marks its signature for a person to look at.
            p_ttd = selection.ttd_guess
            _add_reason(result, REASON_SIGNATURE_PAGE_NOT_FOUND if p_ttd is None else REASON_SIGNATURE_PAGE_GUESSED)
            result.log.p_ttd = p_ttd

        # Identity pages read by OCR that passed; a failed one was already read once with the borders erased.
        reread = [n for n in selection.p_id if n in passed and n != p_ttd and doc.kinds[n] is not PageKind.NATIVE]
        if renderer is not None and ocr is not None and reread:
            _reveal_table_rows(renderer, ocr, selection.p_id, reread, read_pages, quality_name, result)

        result.log.ocr_p_id = engines[selection.p_id[0]]
        result.log.ocr_p_ttd = None if p_ttd is None else engines[p_ttd]
        p_id_conf = min(confidences[n] for n in selection.p_id)
        return _PagesRead(
            {"page_selection": selection.level, "ocr_p_id": ocr_level(p_id_conf)},
            [read_pages[n] for n in selection.p_id if n in read_pages],
            all(n in passed for n in selection.p_id),
            None if p_ttd is None else read_pages.get(p_ttd),
            dict(renderer.turns) if renderer is not None else {},
        )
    finally:
        if renderer is not None:
            renderer.close()


def _reveal_table_rows(
    renderer: PageRenderer,
    ocr: OcrRunner,
    p_id: list[int],
    reread: list[int],
    read_pages: dict[int, PageText],
    quality_name: str,
    result: FileResult,
) -> None:
    """A ruled table can hide whole rows from OCR on a page that passes its quality check: the NIK row, or the
    position. When the identity pages give no NIK or no position, their OCR'd pages are read again with the table
    borders erased, and that read replaces them only if it passes, finds what was missing, and loses nothing found
    before (the NIK, the position, both period dates). On 300 synthetic contracts this found 3 more NIKs, each
    confirmed by the printed birth date and gender, and 2 more positions. Erasing every identity page instead broke 4
    photos (see `OcrRunner.read`)."""
    before = [read_pages[n] for n in p_id if n in read_pages]
    nik, position = find_nik(before), find_position(before)
    if nik.log.status is not NikStatus.MISSING and position.log.status is not PositionStatus.MISSING:
        return
    erased: dict[int, PageText] = {}
    confidences: dict[int, float] = {}
    for number in reread:
        outcome = ocr.read_erased(renderer, number, quality_name)
        if not outcome.passed or outcome.page is None:
            return
        erased[number], confidences[number] = outcome.page, outcome.mean_conf
    after = [erased.get(n, read_pages[n]) for n in p_id if n in read_pages]
    if not _reveals_more(before, after, nik.log.status, position.log.status):
        return
    read_pages.update(erased)
    for number in erased:
        result.log.ocr_conf[number] = round(confidences[number], 3)
        result.log.komponen_halaman.setdefault(number, []).append(f"{ocr.primary.name}+rules_erased@{ocr.read_dpi}dpi")


def _reveals_more(before: list[PageText], after: list[PageText], nik_b: NikStatus, pos_b: PositionStatus) -> bool:
    """The erased read gives a NIK or a position the plain read lacked, and still gives everything the plain read gave.
    A NIK only counts when neither the printed birth date nor gender contradicts it."""
    nik_a, pos_a = find_nik(after).log, find_position(after).log.status
    nik_gained = (
        nik_b is NikStatus.MISSING
        and nik_a.status is NikStatus.FOUND
        and NikCheck.MISMATCH not in (nik_a.tanggal_lahir, nik_a.jenis_kelamin)
    )
    position_gained = pos_b is PositionStatus.MISSING and pos_a is PositionStatus.FOUND
    if not (nik_gained or position_gained):
        return False
    if (nik_b is NikStatus.FOUND and nik_a.status is not NikStatus.FOUND) or (
        pos_b is PositionStatus.FOUND and pos_a is not PositionStatus.FOUND
    ):
        return False
    period_b = find_period(PAGE_SEPARATOR.join(page.text for page in before))
    period_a = find_period(PAGE_SEPARATOR.join(page.text for page in after))
    had_period = period_b.tanggal_mulai and period_b.tanggal_selesai and not period_b.ambigu
    return not had_period or (period_a.tanggal_mulai, period_a.tanggal_selesai, period_a.ambigu) == (
        period_b.tanggal_mulai,
        period_b.tanggal_selesai,
        False,
    )


def _turn_upright(
    renderer: PageRenderer, ocr: OcrRunner, number: int, read_pages: dict[int, PageText], result: FileResult
) -> bool:
    """Turn a page photographed sideways or upside down, so it can be read again; a page that reads upright is never
    turned (see `OcrRunner.best_turn`). Only a page that failed its role pays for this."""
    turn = ocr.best_turn(renderer, number, read_pages.get(number))
    if not turn:
        return False
    renderer.turns[number] = turn
    result.log.putaran_halaman[number] = turn
    return True


def _prepare_extraction(result: FileResult, p_id_pages: list[PageText], max_chars_per_page: int) -> str | None:
    """Tahap 3, local part: regex period from the raw P_id text, then the masked payload for the extractor.

    Returns the only text allowed to leave the machine, or None when masking fails closed. Nothing is sent here.
    """
    if not p_id_pages:
        return None
    result.log.periode_regex = find_period(PAGE_SEPARATOR.join(page.text for page in p_id_pages))
    try:
        masked = mask_pages([page.text for page in p_id_pages], max_chars_per_page)
    except MaskLeakError:
        _add_reason(result, REASON_MASK_LEAK)
        return None
    result.log.pola_dimask = masked.total
    return masked.text


def _read_nik(result: FileResult, p_id_pages: list[PageText]) -> Level:
    """Tahap 3, local part: the NIK from the raw P_id text. It never joins the extractor payload or the log."""
    if not p_id_pages:  # selection ambiguous or no P_id text; that reason already explains the missing NIK
        result.log.nik = NikLog()
        return Level.YELLOW
    read = find_nik(p_id_pages)
    from_text_layer = all(page.source == "text_layer" for page in p_id_pages)
    result.nik = read.nik
    result.log.nik = read.log
    if read.confidence is not None:
        result.confidence["nik"] = round(read.confidence, 3)
    if read.log.status in _NIK_STATUS_REASONS:
        _add_reason(result, _NIK_STATUS_REASONS[read.log.status])
    if read.log.tanggal_lahir is NikCheck.MISMATCH:
        _add_reason(result, REASON_NIK_BIRTH_DATE_MISMATCH)
    if read.log.jenis_kelamin is NikCheck.MISMATCH:
        _add_reason(result, REASON_NIK_GENDER_MISMATCH)
    if read.nik and not from_text_layer:
        _add_reason(result, REASON_NIK_FROM_OCR)
    if read.log.deret_panjang:
        _add_reason(result, REASON_NIK_DIGITS_REPAIRED)
        return _worst(nik_level(read.log, from_text_layer), Level.YELLOW)
    return nik_level(read.log, from_text_layer)


def _worst(*levels: Level) -> Level:
    return max(levels, key=_LEVEL_ORDER.index)


def _png(image: np.ndarray, box: tuple[float, float, float, float]) -> bytes:
    """The part of a page image inside `box` (page fractions), as PNG; a large one is scaled down, never up."""
    height, width = image.shape[:2]
    x0, top, x1, bottom = box
    row0, col0 = int(top * height), int(x0 * width)
    crop = image[row0 : max(row0 + 1, round(bottom * height)), col0 : max(col0 + 1, round(x1 * width))]
    scale = CROP_MAX_SIDE / max(crop.shape[:2])
    if scale < 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise ValueError("png encoding failed")
    return encoded.tobytes()


def _page_image(renderer: PageRenderer, page: PageText, ocr: OcrRunner | None) -> np.ndarray | None:
    """The page as gray pixels in the frame its word boxes use: a text layer rendered like the signature page, an
    OCR'd page straightened exactly as OCR saw it. None when an OCR'd page cannot be rebuilt."""
    if page.source == "text_layer":
        return renderer.render_gray(page.number, SIGNATURE_DPI)
    if ocr is None:
        return None
    return straighten(renderer.render_gray(page.number, ocr.read_dpi))[0]


def _needs_nik_crop(log: NikLog, from_text_layer: bool) -> bool:
    """The local reading is unsure: no NIK, an unreadable one, one picked from a longer run of digits, one the printed
    birth date or gender contradicts, or one read by OCR that the printed birth date and gender do not both confirm.
    Two different NIKs are not a reading problem, and a crop cannot tell whose each one is."""
    if log.status is NikStatus.CONFLICT:
        return False
    checks = (log.tanggal_lahir, log.jenis_kelamin)
    if log.status is not NikStatus.FOUND or log.deret_panjang or NikCheck.MISMATCH in checks:
        return True
    return not from_text_layer and checks != (NikCheck.MATCH, NikCheck.MATCH)


def _needs_second_nik(result: FileResult) -> bool:
    """A NIK the website colours only because OCR may have misread a digit: read from a scan without the printed birth
    date and gender both confirming it, or picked from a longer run of digits. A missing, invalid, contradicted or
    conflicting NIK is not a reading the second reader can confirm."""
    log = result.log.nik
    if log is None or result.nik is None or log.status is not NikStatus.FOUND:
        return False
    checks = (log.tanggal_lahir, log.jenis_kelamin)
    if NikCheck.MISMATCH in checks:
        return False
    unconfirmed = REASON_NIK_FROM_OCR in result.log.alasan and checks != (NikCheck.MATCH, NikCheck.MATCH)
    return unconfirmed or log.deret_panjang


def _needs_second_position(result: FileResult) -> bool:
    """A title the patterns read from a scan with low confidence, which the website colours; never the extractor's."""
    log, confidence = result.log.posisi, result.confidence.get("posisi")
    if result.posisi is None or log is None or log.sumber is PositionSource.LLM or log.cocok_llm is not None:
        return False
    return confidence is not None and confidence < POSITION_SURE


def _same_title(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and " ".join(a.split()) == " ".join(b.split())


def _second_pages(data: bytes, result: FileResult, pages: _PagesRead, ocr: OcrRunner | None) -> list[PageText] | None:
    """The identity pages as the second OCR (RapidOCR) reads them: each scanned page read again, a text-layer page as
    it is. Read at most once per file, whichever field asks first. None without a second reader or a scanned identity
    page, or when a read failed, since a failed read confirms nothing."""
    if pages.second_tried:
        return pages.second
    pages.second_tried = True
    if ocr is None or ocr.second is None or not pages.p_id_pages:
        return None
    scanned = [page.number for page in pages.p_id_pages if page.source.startswith("ocr:")]
    if not scanned:
        return None
    with PageRenderer(data, pages.turns) as renderer:  # a turned photo is read upright, as Tesseract read it
        again = {number: ocr.second_read(renderer, number) for number in scanned}
    for number in scanned:
        result.log.komponen_halaman.setdefault(number, []).append(f"{ocr.second.name}@{ocr.read_dpi}dpi:pembaca_kedua")
    if any(page is None for page in again.values()):
        return None
    pages.second = [again.get(page.number) or page for page in pages.p_id_pages]
    return pages.second


def _second_period(data: bytes, result: FileResult, parsed: ParsedFilename, pages: _PagesRead, ocr: OcrRunner | None) -> None:
    """Tahap 3, second local reader for the period: when Tesseract's text gave the regex no clear period (a date
    missing, nothing contradicting the filename) and the filename's dates are not printed in it either, the regex runs
    on the second OCR's reading too (`log.periode_pembaca_kedua`); `_check_without_extractor` decides with it.

    Only a missing date: a different date is the document disagreeing with the filename, which a second reading cannot
    settle, and several periods are a contradiction. On the 473 synthetic scans and photos (2026-11-09) Tesseract's
    text missed 22 start and 27 end dates, mostly a date broken over a line with OCR noise in between (`s.d 30` /
    `Novemb vember 2044`); in the 2026-11-07 benchmark RapidOCR read 14 of 18 and 19 of 25 such yellow dates right,
    and none wrong."""
    regex = result.log.periode_regex
    if regex is None or regex.ambigu or not (parsed.start and parsed.end) or parsed.flags:
        return
    start, end = regex.tanggal_mulai, regex.tanggal_selesai
    if (start and end) or start not in (None, parsed.start) or end not in (None, parsed.end):
        return
    if _filename_dates_printed(parsed, pages.p_id_pages):
        return  # Tesseract's text confirms it already
    second = _second_pages(data, result, pages, ocr)
    if second is not None:
        result.log.periode_pembaca_kedua = find_period(PAGE_SEPARATOR.join(page.text for page in second))


def _second_confirms_period(
    result: FileResult, parsed: ParsedFilename, start: object, end: object, second_pages: list[PageText] | None
) -> bool:
    """The second OCR's reading fills the dates Tesseract's text missed with the filename's own, and contradicts
    nothing: its regex reads no other date and no second period, and either completes the period or, where it read no
    clear period either, prints both of the filename's dates (the same lookup as on Tesseract's text)."""
    second = result.log.periode_pembaca_kedua
    if second is None or second.ambigu:
        return False
    s_start, s_end = second.tanggal_mulai, second.tanggal_selesai
    if s_start not in (None, parsed.start) or s_end not in (None, parsed.end):
        return False
    if (start or s_start, end or s_end) == (parsed.start, parsed.end):
        return True
    return second_pages is not None and _filename_dates_printed(parsed, second_pages)


def _second_read(data: bytes, result: FileResult, pages: _PagesRead, ocr: OcrRunner | None, level: Level) -> Level:
    """Tahap 3, second local reader: when the NIK or the position Tesseract read from a scan is unsure, the second OCR
    (RapidOCR) reads the identity pages again and the same patterns run on its text. The same value from both engines
    makes it green; anything else leaves it as it was, for the NIK crop or a person. Returns the NIK's level.

    On 800 synthetic contracts (benchmark, 2026-11-07) the two engines read the same value in 269 of the 317 text cells
    Tesseract left yellow, all 269 right; where they differed, RapidOCR was right in 46 of 48 and Tesseract in none,
    so a difference is never settled locally: two of those 48 had both wrong."""
    nik, position = _needs_second_nik(result), _needs_second_position(result)
    if not (nik or position):
        return level
    second = _second_pages(data, result, pages, ocr)
    if second is None:
        return level
    if nik and result.log.nik is not None:
        other = find_nik(second).nik
        result.log.nik.pembaca_kedua = "tidak_terbaca" if other is None else "sama" if other == result.nik else "beda"
        if other == result.nik:
            result.log.alasan = [r for r in result.log.alasan if r not in (REASON_NIK_FROM_OCR, REASON_NIK_DIGITS_REPAIRED)]
            level = nik_level(result.log.nik, True)  # two engines agree on every digit
    if position and result.log.posisi is not None:
        other_title = find_position(second).posisi
        same = _same_title(other_title, result.posisi)
        result.log.posisi.pembaca_kedua = "tidak_terbaca" if other_title is None else "sama" if same else "beda"
    return level


def _read_nik_crop(
    data: bytes, result: FileResult, pages: _PagesRead, extractor: Extractor | None, ocr: OcrRunner | None, level: Level
) -> Level:
    """Tahap 3, crop part: when the local NIK reading is unsure, a crop of only the line holding the NIK digits goes to
    the extractor, never the page. Its NIK must pass the same local checks to be used: in place of a missing or
    contradicted local reading, and green only when the printed birth date and gender both confirm it. The same
    digits as the local reading count as a text layer's; other digits that cannot replace it make the NIK yellow."""
    local, p_id_pages = result.log.nik, pages.p_id_pages
    if extractor is None or local is None or not p_id_pages or local.pembaca_kedua == "sama":
        return level  # two local engines already agree on every digit: nothing is sent
    if not _needs_nik_crop(local, all(page.source == "text_layer" for page in p_id_pages)):
        return level
    spot = nik_spot(p_id_pages)
    if spot is None:
        return level
    page = p_id_pages[spot.page]
    with PageRenderer(data, pages.turns) as renderer:
        image = _page_image(renderer, page, ocr)
    if image is None:
        return level
    result.log.komponen_halaman.setdefault(page.number, []).append(f"{extractor.name}:potongan_nik")
    try:
        answer = extractor.read_image(ImageTask.NIK, _png(image, spot.box))
    except ExtractorError as exc:
        _count_call(result, extractor, "nik", exc.usage)
        _add_reason(result, f"{REASON_EXTRACTOR_ERROR}:{exc.kind}")
        return level
    _count_call(result, extractor, "nik", answer.usage)
    if answer.nik is None:
        local.llm = "tidak_terbaca"
        return level
    if answer.nik == result.nik:
        local.llm = "sama"
        result.log.alasan = [r for r in result.log.alasan if r not in (REASON_NIK_FROM_OCR, REASON_NIK_DIGITS_REPAIRED)]
        return nik_level(local, True)  # two readers agree on every digit
    checked = check_nik(answer.nik, p_id_pages, spot)
    checks = (checked.tanggal_lahir, checked.jenis_kelamin)
    confirmed = checks == (NikCheck.MATCH, NikCheck.MATCH)
    if checked.status is NikStatus.FOUND and NikCheck.MISMATCH not in checks and (result.nik is None or confirmed):
        checked.llm = "dipakai"
        result.nik, result.log.nik = answer.nik, checked
        result.confidence["nik"] = round(answer.confidence, 3)
        result.log.alasan = [r for r in result.log.alasan if r not in _LOCAL_NIK_REASONS]
        _add_reason(result, REASON_NIK_FROM_LLM)
        return Level.GREEN if confirmed else Level.YELLOW
    local.llm = "beda"
    _add_reason(result, REASON_NIK_LLM_DIFFERS)
    return _worst(level, Level.YELLOW)


def _read_position(result: FileResult, p_id_pages: list[PageText]) -> None:
    """The employee's position from the raw P_id text; an extra output that does not gate the decision."""
    if not p_id_pages:
        result.log.posisi = PositionLog()
        return
    read = find_position(p_id_pages)
    result.posisi = read.posisi
    result.log.posisi = read.log
    if read.confidence is not None:
        result.confidence["posisi"] = round(read.confidence, 3)


def _filename_level(result: FileResult, parsed: ParsedFilename) -> Level:
    """Tahap 5a: compare the extracted name and period with the filename `Nama Karyawan - Masa Kontrak`."""
    if FLAG_NONSTANDARD in parsed.flags:
        return Level.YELLOW  # the whole stem is treated as the name, so a comparison would mean nothing
    name_match = compare_name(parsed.name, result.nama_karyawan or "")
    period_match = compare_period(parsed, result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan)
    result.log.validasi_nama_file = {"nama": name_match.value}
    if period_match is not None:
        result.log.validasi_nama_file["periode"] = period_match.value
    return filename_level(name_match, period_match, parsed.flags)


def _name_on_page(result: FileResult, parsed: ParsedFilename, p_id_pages: list[PageText]) -> bool | None:
    """Whether a standard filename's name is printed on the identity page; None when there is no name to look for."""
    if FLAG_NONSTANDARD in parsed.flags or FLAG_UNPARSED in parsed.flags or not parsed.name:
        return None
    name_norm = normalize_name(parsed.name)
    found = any(name_printed(name_norm, line.text) for page in p_id_pages for line in page.lines)
    result.log.validasi_nama_file["nama"] = (Match.COCOK if found else Match.TIDAK_COCOK).value
    return found


def _name_on_signature_page(
    result: FileResult, parsed: ParsedFilename, p_id_pages: list[PageText], p_ttd_page: PageText | None
) -> bool:
    """A standard filename's name the identity page does not show, looked for in the employee's column of the
    signature block (`employee_name_printed`). Only on a signature page apart from the identity pages, which were
    searched already."""
    if p_ttd_page is None or result.log.p_ttd is None or result.log.p_ttd in result.log.p_id:
        return False
    labels = employee_labels(PAGE_SEPARATOR.join(page.text for page in p_id_pages))
    if not employee_name_printed(p_ttd_page, parsed.name, labels):
        return False
    result.log.validasi_nama_file["nama"] = Match.COCOK.value
    result.log.validasi_nama_file[NAME_PAGE_KEY] = NAME_PAGE_SIGNATURE
    return True


def _filename_dates_printed(parsed: ParsedFilename, p_id_pages: list[PageText]) -> bool:
    """Both of the filename's dates are written somewhere on the identity page, in any date format."""
    if not (parsed.start and parsed.end):
        return False
    printed = dates_in_text(PAGE_SEPARATOR.join(page.text for page in p_id_pages))
    return parsed.start in printed and parsed.end in printed


def _check_without_extractor(
    result: FileResult,
    parsed: ParsedFilename,
    p_id_pages: list[PageText],
    p_ttd_page: PageText | None = None,
    second_pages: list[PageText] | None = None,
) -> Level:
    """Pattern mode: the filename's name and period count once the document text confirms them on its own.

    Checking a known value is safer than reading one: the name from a standard filename must be printed on the
    identity page, and so must the filename's period, either as the period the local regex reads or, where the regex
    read no clear period, as both of the filename's dates written on the page. The regex still vetoes: several
    periods, or a whole different one, is a contradiction. Then the filename's values fill the output fields and the
    extraction is green; a missing piece is yellow, a contradiction red. On 300 synthetic contracts the date lookup
    confirmed 5 more periods than the regex alone and confirmed no wrong period: not another contract's, and not one
    a month off.

    Where Tesseract's text left a date unread, the second OCR's reading of the identity pages may complete the period
    (`_second_confirms_period`), never change it. A name the identity page does not show may still be confirmed in the employee's column of the signature page, but
    only once the identity page confirmed the period: a name missing there with no period either may mean the wrong
    page was taken for the identity page.
    """
    result.log.pembaca = Reader.PATTERN
    name_found = _name_on_page(result, parsed, p_id_pages)
    if name_found is None:
        return Level.YELLOW  # nothing trustworthy to confirm
    regex = result.log.periode_regex
    start, end = (regex.tanggal_mulai, regex.tanggal_selesai) if regex else (None, None)
    regex_agrees_so_far = not (regex and regex.ambigu) and start in (None, parsed.start) and end in (None, parsed.end)
    if not (start and end) and regex_agrees_so_far and _filename_dates_printed(parsed, p_id_pages):
        start, end = parsed.start, parsed.end  # the regex missed the clause; the dates are printed all the same
    elif not (start and end) and regex_agrees_so_far and _second_confirms_period(result, parsed, start, end, second_pages):
        start, end = parsed.start, parsed.end
        result.log.validasi_nama_file[PERIOD_FROM_KEY] = PERIOD_FROM_SECOND
    period = compare_period(parsed, start, end, regex.durasi_bulan if regex else None)
    if period is not None and (period is not Match.TIDAK_COCOK or (start and end)):
        result.log.validasi_nama_file["periode"] = period.value  # the website shows it next to the dates
    if not name_found and period is Match.COCOK and not (regex and regex.ambigu):
        name_found = _name_on_signature_page(result, parsed, p_id_pages, p_ttd_page)

    level = Level.GREEN
    if not name_found:
        _add_reason(result, REASON_NAME_NOT_IN_SOURCE)
        level = Level.YELLOW
    if regex is not None and regex.ambigu:
        _add_reason(result, REASON_PERIOD_CONFLICT)
        return Level.RED
    if period is Match.TIDAK_COCOK and start and end:
        result.flags.append(FLAG_FILENAME_MISMATCH)  # the document states another period than the filename
        return Level.RED
    if not (start and end):
        _add_reason(result, REASON_PERIOD_NOT_IN_SOURCE)
        level = Level.YELLOW
    elif period is not Match.COCOK:  # a few days apart: close, but a person decides
        level = Level.YELLOW
    if level is Level.GREEN:
        result.nama_karyawan = parsed.name
        result.tanggal_mulai, result.tanggal_selesai = start, end
        result.durasi_bulan = (regex.durasi_bulan if regex else None) or months_between(start, end)
    return level


def _count_call(result: FileResult, extractor: Extractor, call: str, usage: TokenUsage | None) -> None:
    """One more extractor call for this file, and its tokens added to the file's total."""
    result.log.extractor = extractor.name
    result.log.panggilan_llm.append(call)
    result.log.token_panggilan.append(usage)
    if usage is not None:
        result.log.token_llm = usage if result.log.token_llm is None else result.log.token_llm.plus(usage)


def _extract(
    result: FileResult,
    payload: str | None,
    trusted: bool,
    parsed: ParsedFilename,
    extractor: Extractor | None,
    p_id_pages: list[PageText],
    assist: bool = False,
    p_ttd_page: PageText | None = None,
    second_pages: list[PageText] | None = None,
) -> dict[str, Level]:
    """Tahap 3, cloud part: send the masked payload and verify the answer locally. Whenever nothing is sent or the
    call fails, the file is read the local way (pattern mode), so the extractor never leaves a file worse off.

    `assist` is the website's flow, where the local reading is the main one and the extractor only a fallback (owner
    2026-11-11). The patterns check the name and period first, and both local OCR engines have read the position;
    the masked text goes out only when something the text call reads is still unsure: the name or period not confirmed
    (yellow), the title unsure (`_position_unsure`), or the vendor folder not printed on the pages read. The answer
    then fills only those fields: the name and period when the patterns left them yellow, the title when the local one
    is unsure, and the employer company for the vendor check (`_check_vendor`). On the 800 synthetic v2 contracts with
    the reader of 2026-11-10, 31 files had such a field, against a text call for every file before."""
    if payload is None:  # selection ambiguous, mask_leak, or no P_id text: nothing to send
        return {"extraction": Level.RED}
    if extractor is None:
        _add_reason(result, REASON_EXTRACTOR_DISABLED)
        return {"extraction": _check_without_extractor(result, parsed, p_id_pages, p_ttd_page, second_pages)}
    if not trusted:
        # Low-quality OCR can split a NIK into short digit runs that masking misses, so the text stays local.
        _add_reason(result, REASON_EXTRACTOR_NOT_SENT)
        return {"extraction": _check_without_extractor(result, parsed, p_id_pages, p_ttd_page, second_pages)}
    pattern_level = _check_without_extractor(result, parsed, p_id_pages, p_ttd_page, second_pages) if assist else None
    title_unsure = assist and _position_unsure(result)
    if assist and pattern_level is not Level.YELLOW and not title_unsure:
        if _vendor_printed(result, p_id_pages, p_ttd_page) is not False:
            _add_reason(result, REASON_EXTRACTOR_NOT_NEEDED)
            return {"extraction": pattern_level}

    result.log.teks_keluar_karakter = len(payload)  # counted on send: a failed call may still have delivered it
    for number in result.log.p_id:
        result.log.komponen_halaman.setdefault(number, []).append(f"{extractor.name}:masked")
    try:
        extraction = extractor.extract(payload)
    except ExtractorError as exc:
        _count_call(result, extractor, "teks", exc.usage)
        _add_reason(result, f"{REASON_EXTRACTOR_ERROR}:{exc.kind}")
        level = pattern_level or _check_without_extractor(result, parsed, p_id_pages, p_ttd_page, second_pages)
        return {"extraction": level}
    _count_call(result, extractor, "teks", extraction.usage)
    verified = verify_extraction(extraction, payload, result.log.periode_regex)
    result.vendor_dokumen = verified.vendor  # decides only a vendor folder the pages do not print (`_check_vendor`)
    if not assist or title_unsure:
        _merge_position(result, verified)
    if pattern_level is not None and pattern_level is not Level.YELLOW:
        return {"extraction": pattern_level}
    if pattern_level is not None:
        # The extractor's reading replaces the patterns' answer, and its verification adds its own reasons.
        result.log.alasan = [r for r in result.log.alasan if r not in _PATTERN_REASONS]
        result.log.validasi_nama_file = {}
        result.log.llm_cadangan = True
    return _use_extraction(result, verified, extraction, parsed, p_id_pages)


def _use_extraction(
    result: FileResult,
    verified: VerifiedExtraction,
    extraction: ExtractionResult,
    parsed: ParsedFilename,
    p_id_pages: list[PageText],
) -> dict[str, Level]:
    """The extractor's name and period, verified, become the file's."""
    result.log.pembaca = Reader.LLM
    written_end = verified.end if verified.end == extraction.tanggal_selesai.nilai else None
    result.log.periode_llm = LlmPeriod(
        tanggal_mulai=verified.start, tanggal_selesai=written_end, durasi_bulan=verified.written_months
    )
    result.nama_karyawan = verified.name
    result.tanggal_mulai, result.tanggal_selesai = verified.start, verified.end
    result.durasi_bulan = verified.duration_months
    result.confidence["nama"] = verified.confidence["nama"]
    result.confidence["periode"] = verified.confidence["periode"]
    for reason in verified.reasons:
        _add_reason(result, reason)

    levels: dict[str, Level] = {}
    if verified.name:
        levels[COMPONENT_FILENAME] = _filename_level(result, parsed)
    else:  # nothing to compare; still say whether the filename's name is printed, as pattern mode does
        _name_on_page(result, parsed, p_id_pages)
    standard = FLAG_NONSTANDARD not in parsed.flags and FLAG_UNPARSED not in parsed.flags
    period_match = (
        compare_period(parsed, verified.start, verified.end, verified.duration_months) if standard else None
    )
    levels["extraction"] = extraction_level(verified, period_match)
    return levels


def _merge_position(result: FileResult, verified: VerifiedExtraction) -> None:
    """The extractor's verified title wins, since it tells the employee's title from a placement or a department; the
    local reading stays only as a check. Without a verified title from the extractor, the local reading is kept."""
    title = verified.position
    if title is None:
        return
    local = result.log.posisi or PositionLog()
    if result.posisi is not None:
        local.cocok_llm = fuzz.token_set_ratio(result.posisi.casefold(), title.casefold()) >= SAME_POSITION
        local.ocr_ragu = _unclear_scan_title(result)
    elif local.status is PositionStatus.CONFLICT:
        local.cocok_llm = False  # the local reading found two titles; the extractor picked one of them
    else:
        local.sumber = PositionSource.LLM
    local.status = PositionStatus.FOUND
    local.koreksi_llm = verified.position_fixed
    result.posisi = title
    result.log.posisi = local
    result.confidence["posisi"] = round(verified.confidence["posisi"], 3)


def _position_unsure(result: FileResult) -> bool:
    """Whether the website colours the local title (backend `fields.ts`, `position`): none read, two different titles,
    or a title read from a scan with low confidence that the second OCR did not read the same."""
    log = result.log.posisi
    if result.posisi is None or log is None or log.status is PositionStatus.CONFLICT:
        return True
    return _unclear_scan_title(result)


def _unclear_scan_title(result: FileResult) -> bool:
    """A local title read from a scan with low confidence that the second OCR did not read the same.

    The extractor reads the same OCR text, so its agreement does not confirm such a title (`log.posisi.ocr_ragu`). On
    the 2026-11-11 v2 run (800 synthetic contracts, Claude as fallback) it agreed with 5 of them: 4 right and
    `Cleaning Service 1`, which went green."""
    log, confidence = result.log.posisi, result.confidence.get("posisi")
    return log is not None and confidence is not None and confidence < POSITION_SURE and log.pembaca_kedua != "sama"


def _vendor_printed(result: FileResult, p_id_pages: list[PageText], p_ttd_page: PageText | None) -> bool | None:
    """Whether the vendor folder's name is printed on the identity or signature page; None when there is no vendor
    folder or no page text."""
    if not result.vendor or not p_id_pages:
        return None
    texts = [page.text for page in p_id_pages] + ([p_ttd_page.text] if p_ttd_page else [])
    return vendor_in_text(result.vendor, PAGE_SEPARATOR.join(texts))


def _check_vendor(result: FileResult, pages: _PagesRead) -> Level | None:
    """The vendor folder against the document, all local: the folder's name looked up in the identity and signature
    pages' text first, then, when it is not printed there, the employer company the extractor read. Not finding it is
    yellow, since a folder may be named by an abbreviation; None when there is no vendor folder or no page text.

    The lookup goes first because it checks a known value: on 300 synthetic contracts it confirmed 259 of 259 vendor
    folders and matched no wrong one. The extractor's employer only decides a file the lookup could not confirm."""
    printed = _vendor_printed(result, pages.p_id_pages, pages.p_ttd_page)
    if printed is None:
        return None
    same, reason = printed, REASON_VENDOR_NOT_IN_SOURCE
    result.log.vendor_dari = "teks"
    if not same and result.vendor_dokumen:
        same, reason = vendor_in_text(result.vendor, result.vendor_dokumen), REASON_VENDOR_MISMATCH
        result.log.vendor_dari = "llm"
    result.log.validasi_vendor = (Match.COCOK if same else Match.TIDAK_COCOK).value
    if same:
        return Level.GREEN
    _add_reason(result, reason)
    return Level.YELLOW


def _signature_crop(
    result: FileResult, extractor: Extractor, image: np.ndarray, roi: tuple[float, float, float, float], number: int
) -> SignatureStatus | None:
    """The extractor's look at the signature area, a little wider and taller than the ROI so a large signature fits;
    never the page. Returns the status it decides (see `_signature`), or None to keep the local one."""
    x0, top, x1, bottom = roi
    width, height = x1 - x0, bottom - top
    box = (max(0.0, x0 - 0.1 * width), max(0.0, top - 0.3 * height), min(1.0, x1 + 0.1 * width), min(1.0, bottom + 0.05 * height))
    result.log.komponen_halaman.setdefault(number, []).append(f"{extractor.name}:potongan_ttd")
    try:
        answer = extractor.read_image(ImageTask.SIGNATURE, _png(image, box))
    except ExtractorError as exc:
        _count_call(result, extractor, "ttd", exc.usage)
        _add_reason(result, f"{REASON_EXTRACTOR_ERROR}:{exc.kind}")
        return None
    _count_call(result, extractor, "ttd", answer.usage)
    if result.log.tanda_tangan is not None:
        result.log.tanda_tangan.llm, result.log.tanda_tangan.llm_conf = answer.status, round(answer.confidence, 3)
    signed = answer.status == "signed" and answer.confidence >= LLM_SIGNED_CONF
    unsigned = answer.status == "unsigned" and answer.confidence >= LLM_UNSIGNED_CONF
    if result.status_ttd is SignatureStatus.UNCERTAIN:
        return SignatureStatus.SIGNED if signed else SignatureStatus.UNSIGNED if unsigned else None
    if result.status_ttd is SignatureStatus.SIGNED:
        # A wrong "signed" lets an unsigned contract through, so any "unsigned" sends it to a person, however unsure.
        return SignatureStatus.UNCERTAIN if answer.status == "unsigned" else None
    return SignatureStatus.UNCERTAIN if signed else None  # read unsigned locally: two readers disagree


def _agreement_settles(result: FileResult) -> bool:
    """A local signed or unsigned the ink check called not clear (`REASON_SIGNATURE_NOT_CLEAR`) turns sure when the
    extractor's look at the crop said the same at `LLM_AGREE_CONF` or more (`REASON_SIGNATURE_LLM_AGREES`). Returns
    whether it did; anything else leaves the result as it was."""
    look = result.log.tanda_tangan
    if REASON_SIGNATURE_NOT_CLEAR not in result.log.alasan or look is None or look.llm_conf is None:
        return False
    if look.llm not in ("signed", "unsigned") or look.llm != result.status_ttd.value or look.llm_conf < LLM_AGREE_CONF:
        return False
    result.log.alasan = [r for r in result.log.alasan if r != REASON_SIGNATURE_NOT_CLEAR]
    _add_reason(result, REASON_SIGNATURE_LLM_AGREES)
    result.confidence["ttd"] = look.llm_conf
    return True


def _signature(
    data: bytes,
    doc: DocumentRead,
    result: FileResult,
    pages: _PagesRead,
    parsed: ParsedFilename,
    ocr: OcrRunner | None,
    extractor: Extractor | None = None,
) -> Level:
    """Tahap 4, local first. With `extractor` (the website's flow), a crop of the signature area goes to it for every
    scan and photo, when the local check is unsure, and when a text-layer PDF reads unsigned (an e-signature can look
    like no ink). Sure enough, it decides an unsure status. When it disagrees with a local signed or unsigned, the
    status becomes uncertain; when it agrees or is unsure itself, the local status stays, and an agreement sure enough
    also settles a local status the ink check called not clear (`_agreement_settles`)."""
    page = pages.p_ttd_page
    number = result.log.p_ttd
    if page is None or number is None:
        result.status_ttd = SignatureStatus.UNCERTAIN
        if not any(r in result.log.alasan for r in _PAGE_NOT_FOUND):  # that reason already explains it
            _add_reason(result, REASON_SIGNATURE_PAGE_UNREADABLE)
        return Level.RED

    native = doc.kinds[number] is PageKind.NATIVE
    dpi = SIGNATURE_DPI if native or ocr is None else ocr.read_dpi
    with PageRenderer(data, pages.turns) as renderer:  # a turned photo is read upright, as OCR read it
        image = renderer.render_gray(number, dpi)
        colour = renderer.render_rgb(number, dpi)
    stamps, whiten = find_meterai(colour, image)
    if not native:
        image, angle = straighten(image)  # the same transform OCR saw, so its word boxes line up with these pixels
        whiten = deskew((~whiten).astype(np.uint8) * 255, angle) < 128  # white border = nothing to whiten
    plain = image  # a signature across the meterai is still a signature to the extractor
    image = np.where(whiten, np.uint8(255), image)
    height, width = colour.shape[:2]
    meterai = [Box(x0 / width, y0 / height, x1 / width, y1 / height) for x0, y0, x1, y1 in stamps]
    result.log.komponen_halaman.setdefault(number, []).append(f"signature@{dpi}dpi")

    # The verified extracted name anchors the ROI; a standard filename's name is the fallback.
    name = result.nama_karyawan or (None if FLAG_NONSTANDARD in parsed.flags else parsed.name) or None
    labels = employee_labels(PAGE_SEPARATOR.join(p.text for p in pages.p_id_pages))
    found = detect_signature(page, native, image, read_page_objects(data, number), name, labels, meterai)

    result.status_ttd = found.status
    result.confidence["ttd"] = found.confidence
    result.log.tanda_tangan = SignatureLog(
        sumber=found.source,
        jangkar=found.anchor,
        roi=tuple(round(v, 4) for v in found.roi) if found.roi else None,  # type: ignore[arg-type]
        rasio_tinta=round(found.features.ink_ratio, 4) if found.features else None,
        komponen_tertinggi=round(found.features.tallest, 2) if found.features else None,
        anotasi_di_luar_roi=found.annotations_outside_roi,
        kandidat_roi=found.candidates,
        meterai=found.meterai,
    )
    if not found.anchor_found:
        _add_reason(result, REASON_SIGNATURE_ANCHOR_NOT_FOUND)
    elif found.status is SignatureStatus.UNCERTAIN:
        _add_reason(result, REASON_SIGNATURE_UNCERTAIN)
    elif not found.clear_margin:
        _add_reason(result, REASON_SIGNATURE_NOT_CLEAR)
    level = signature_level(found.status, found.anchor_found, found.clear_margin)

    # A native page that reads signed has a drawn signature on it, and the ink check agreed; scans and photos carry
    # dither, meterai patterns and faint strokes the ink check can misjudge either way.
    unsure = found.status is SignatureStatus.UNCERTAIN or (native and found.status is SignatureStatus.UNSIGNED)
    if extractor is None or not found.anchor_found or found.roi is None or not (unsure or not native):
        return level
    decided = _signature_crop(result, extractor, plain, found.roi, number)
    if decided is None:
        return Level.GREEN if _agreement_settles(result) else level
    result.status_ttd = decided
    result.log.alasan = [r for r in result.log.alasan if r not in (REASON_SIGNATURE_UNCERTAIN, REASON_SIGNATURE_NOT_CLEAR)]
    if decided is SignatureStatus.UNCERTAIN:
        sees_none = found.status is SignatureStatus.SIGNED
        _add_reason(result, REASON_SIGNATURE_LLM_SEES_NONE if sees_none else REASON_SIGNATURE_LLM_DIFFERS)
        return Level.YELLOW
    _add_reason(result, REASON_SIGNATURE_FROM_LLM)
    if result.log.tanda_tangan is not None and result.log.tanda_tangan.llm_conf is not None:
        result.confidence["ttd"] = result.log.tanda_tangan.llm_conf
    return Level.GREEN


def _confirm_page_selection(result: FileResult, levels: dict[str, Level]) -> None:
    """A close page score matters less once both chosen pages proved right by what they hold: the identity page
    confirmed the name and period, and the signature block printed the employee's name. Red is never lifted."""
    if levels.get("page_selection") is not Level.YELLOW:
        return
    signature = result.log.tanda_tangan
    if levels.get("extraction") is Level.GREEN and signature is not None and signature.jangkar == ANCHOR_NAME:
        levels["page_selection"] = Level.GREEN
    else:
        _add_reason(result, REASON_PAGE_SELECTION_LOW_MARGIN)


def process_file(
    path: Path,
    settings: Settings,
    ocr: OcrRunner | None = None,
    extractor: Extractor | None = None,
    vendor: str | None = None,
) -> FileResult:
    """`ocr=None` means local OCR is not available; scan and hybrid pages are then flagged, not guessed.
    `extractor=None` means no text leaves the machine; extraction then counts as red. `vendor` is the vendor folder
    the file sits in (`root/<vendor>/...`), checked against the document; None skips that check."""

    def load() -> bytes:
        validate_pdf_file(path, _max_bytes(settings))
        return path.read_bytes()

    return _process(path.name, load, settings, ocr, extractor, vendor)


def process_bytes(
    data: bytes,
    filename: str,
    settings: Settings,
    ocr: OcrRunner | None = None,
    extractor: Extractor | None = None,
    vendor: str | None = None,
) -> FileResult:
    """`process_file` for a PDF already in memory, such as one streamed from SharePoint; it is never written to
    disk. `filename` is only used for the filename checks and the result, never opened."""

    def load() -> bytes:
        validate_pdf_bytes(data, filename, _max_bytes(settings))
        return data

    return _process(filename, load, settings, ocr, extractor, vendor)


def _max_bytes(settings: Settings) -> int:
    return settings.input.max_file_mb * 1024 * 1024


def _process(
    filename: str,
    load: Callable[[], bytes],
    settings: Settings,
    ocr: OcrRunner | None,
    extractor: Extractor | None,
    vendor: str | None = None,
) -> FileResult:
    result = FileResult(file=filename, vendor=" ".join((vendor or "").split()) or None)
    fid = file_id(filename)
    try:
        parsed = parse_filename(filename)
        result.entitas = parsed.entity  # copied from the filename, so an unreadable PDF still keeps it
        data = load()
        doc = read_document(data, settings.input.max_pages, settings.page_selection.economy_mode)
        result.log.n_halaman = doc.n_pages
        result.log.jenis_halaman = doc.kinds
        result.log.format_halaman = doc.issues
        result.catatan_vendor = vendor_note(doc.issues)
        if doc.issues:
            result.flags.append(FLAG_FORMAT_NONSTANDARD)

        result.flags.extend(parsed.flags)
        # A nonstandard filename's "name" may include period text, so it cannot vouch for OCR quality.
        quality_name = "" if FLAG_NONSTANDARD in parsed.flags else normalize_name(parsed.name)

        pages = _select_and_read(data, doc, result, parsed.name, quality_name, ocr)
        levels = dict(pages.levels)
        if doc.issues:
            levels[COMPONENT_FORMAT] = Level.YELLOW  # read by OCR, but a person checks every non-standard file
        payload =_prepare_extraction(result, pages.p_id_pages, settings.extractor.max_chars_per_page)
        _second_period(data, result, parsed, pages, ocr)
        levels[COMPONENT_NIK] = _read_nik(result, pages.p_id_pages)
        _read_position(result, pages.p_id_pages)
        # Both local OCR engines before the extractor: in the website's flow it is only asked what they leave unsure.
        levels[COMPONENT_NIK] = _second_read(data, result, pages, ocr, levels[COMPONENT_NIK])
        assist = settings.extractor.assist
        levels.update(
            _extract(
                result, payload, pages.p_id_trusted, parsed, extractor, pages.p_id_pages, assist, pages.p_ttd_page,
                pages.second,
            )
        )
        if assist:  # crops go to the extractor in the website's flow only; the CLI's extractor mode sends text alone
            levels[COMPONENT_NIK] = _read_nik_crop(data, result, pages, extractor, ocr, levels[COMPONENT_NIK])
        vendor_level = _check_vendor(result, pages)
        if vendor_level is not None:
            levels[COMPONENT_VENDOR] = vendor_level
        levels["signature"] = _signature(data, doc, result, pages, parsed, ocr, extractor if assist else None)
        _confirm_page_selection(result, levels)

        result.keputusan, extra_flags = decide(levels)
        result.flags.extend(extra_flags)
        if result.keputusan is Decision.AUTO and not settings.decision.allow_auto:
            result.keputusan = Decision.REVIEW
            result.flags.append(FLAG_AUTO_DISABLED)
    except InputRejected as exc:
        result.flags.append(FLAG_INPUT_REJECTED)
        result.log.alasan.append(exc.reason)
        log.warning("file %s rejected: %s", fid, exc.reason)
    except Exception as exc:  # FR-1: isolate every per-file failure; record the type only, never the message
        result.flags.append(FLAG_PROCESSING_ERROR)
        result.log.alasan.append(f"error:{type(exc).__name__}")
        log.error("file %s failed: %s", fid, type(exc).__name__)
    return result


def collect_inputs(input_path: Path, recursive: bool = False) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in input_path.glob(pattern) if p.suffix.lower() == ".pdf" and p.is_file() and not p.is_symlink()
    )


def process_path(
    input_path: Path,
    settings: Settings,
    recursive: bool = False,
    ocr: OcrRunner | None = None,
    extractor: Extractor | None = None,
    vendor_folders: bool = False,
) -> list[FileResult]:
    """`vendor_folders`: the input folder is laid out like the website's root, `<vendor>/<masa kontrak>/<pdf>`, so the
    first folder under it is each file's vendor and is checked against the document. Off for any other layout."""
    files = collect_inputs(input_path, recursive)
    log.info("processing %d file(s)", len(files))
    root = input_path if input_path.is_dir() else input_path.parent
    results = []
    for path in files:
        parts = path.relative_to(root).parts
        vendor = parts[0] if vendor_folders and len(parts) > 1 else None
        result = process_file(path, settings, ocr, extractor, vendor)
        # Subfolders can hold files with the same name; the relative path keeps every output row traceable.
        result.file = path.relative_to(root).as_posix()
        results.append(result)
    return results
