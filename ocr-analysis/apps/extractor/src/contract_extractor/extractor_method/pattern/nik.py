"""Tahap 3, jalur lokal: NIK karyawan dari teks P_id mentah, sebelum masking.

NIK tidak pernah masuk payload extractor dan tidak pernah ditulis ke log. Angka yang dibaca harus berstruktur NIK
(kode provinsi, kabupaten/kota, kecamatan, tanggal lahir dengan +40 untuk perempuan, nomor urut), lalu dicocokkan
dengan tanggal lahir dan jenis kelamin yang tercetak di blok identitas. Dua NIK valid yang berbeda membuat hasilnya
null, bukan memilih salah satu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from contract_extractor.common.dates import safe_date
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import NikCheck, NikLog, NikStatus
from contract_extractor.extractor_method.llm.masking import PAGE_SEPARATOR
from contract_extractor.extractor_method.pattern.period_regex import birth_dates

NIK_DIGITS = 16
# Province codes on KTPs, including the Papua provinces created in 2022.
PROVINCE_CODES = frozenset(
    {11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 31, 32, 33, 34, 35, 36, 51, 52, 53, 61, 62, 63, 64, 65}
    | {71, 72, 73, 74, 75, 76, 81, 82, 91, 92, 93, 94, 95, 96, 97}
)
FEMALE_DAY_OFFSET = 40
MIN_REAL_DIGITS = 12  # an OCR-damaged run still has to be mostly digits
NEXT_LINE_LEAD = 4  # a value wrapped onto the next line starts within this many characters of it (`| 3171...`)
GENDER_WINDOW = 40  # after `jenis kelamin`
SENTENCE_GENDER_WINDOW = 200  # before an NIK label in a sentence: `Budi, laki-laki, lahir di ..., pemegang NIK ...`

# `NIK` (also OCR'd as `N1K`), `No. KTP`, `Nomor KTP`, `No. Induk Kependudukan`.
_LABEL = re.compile(
    r"(?<![a-z])(?:n[i1l|!]k|no\.?\s*ktp|nomor\s*ktp|(?:no\.?|nomor)\s*induk\s*kependudukan)(?![a-z])", re.IGNORECASE
)
_EXACT = re.compile(rf"(?<!\d)\d{{{NIK_DIGITS}}}(?!\d)")
# Letters Tesseract returns for digits on scans. A run may hold one separator between characters, and a colon read
# as `1` makes it longer than 16, so every 16-digit window of the run is tried.
_SLIPS = str.maketrans("OoDQIl|!iSsBZzGbgq", "000011111558226699")
_DIGIT_LIKE = r"[0-9OoDQIl|!iSsBZzGbgq]"
_RUN = re.compile(rf"(?<![0-9A-Za-z]){_DIGIT_LIKE}(?:[ .\-]?{_DIGIT_LIKE}){{13,19}}(?![0-9A-Za-z])")
_SEPARATORS = re.compile(r"[ .\-]")
_GENDER_LABEL = re.compile(r"jenis\s+kelamin", re.IGNORECASE)
_MALE = re.compile(r"(?<![a-z])(?:laki[\s-]*laki|pria)(?![a-z])", re.IGNORECASE)
_FEMALE = re.compile(r"(?<![a-z])(?:perempuan|wanita)(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class NikRead:
    nik: str | None
    log: NikLog
    confidence: float | None  # lowest OCR word confidence over the NIK (1.0 from a text layer); None without a NIK


@dataclass(frozen=True)
class _Coded:
    """What a structurally valid NIK says about its holder."""

    day: int
    month: int
    year: int  # two digits
    female: bool


def mask_nik(nik: str) -> str:
    """The form shown and exported by default: the last four digits only."""
    return "*" * (len(nik) - 4) + nik[-4:]


def decode(nik: str) -> _Coded | None:
    """The birth date and gender coded in a structurally valid NIK, else None."""
    if len(nik) != NIK_DIGITS or not nik.isdigit():
        return None
    province, regency, district, serial = int(nik[:2]), int(nik[2:4]), int(nik[4:6]), int(nik[12:])
    day, month, year = int(nik[6:8]), int(nik[8:10]), int(nik[10:12])
    female = day > FEMALE_DAY_OFFSET
    day -= FEMALE_DAY_OFFSET if female else 0
    if province not in PROVINCE_CODES or not (regency and district and serial):
        return None
    if not any(safe_date(century + year, month, day) for century in (1900, 2000)):
        return None
    return _Coded(day, month, year, female)


def joined_words(pages: list[PageText]) -> tuple[str, list[tuple[int, int, float]]]:
    """The P_id text exactly as the other local stages see it, plus each word's span and confidence."""
    spans: list[tuple[int, int, float]] = []
    pos = 0
    for i, page in enumerate(pages):
        pos += len(PAGE_SEPARATOR) if i else 0
        for j, line in enumerate(page.content_lines):
            pos += 1 if j else 0
            for k, word in enumerate(line.words):
                pos += 1 if k else 0
                spans.append((pos, pos + len(word.text), word.conf))
                pos += len(word.text)
    return PAGE_SEPARATOR.join(page.text for page in pages), spans


def _birth_check(coded: _Coded, births: set[date]) -> NikCheck:
    if not births:
        return NikCheck.ABSENT
    same = any((b.day, b.month, b.year % 100) == (coded.day, coded.month, coded.year) for b in births)
    return NikCheck.MATCH if same else NikCheck.MISMATCH


def _printed_genders(text: str, label_start: int) -> set[bool]:
    """Genders printed after `jenis kelamin`, or in the sentence before the NIK label; True means female."""
    found: set[bool] = set()
    windows = [(m.end(), m.end() + GENDER_WINDOW) for m in _GENDER_LABEL.finditer(text)]
    windows.append((max(0, label_start - SENTENCE_GENDER_WINDOW), label_start))
    for start, end in windows:
        if _MALE.search(text, start, end):
            found.add(False)
        if _FEMALE.search(text, start, end):
            found.add(True)
    return found


def _gender_check(coded: _Coded, printed: set[bool]) -> NikCheck:
    if len(printed) != 1:
        return NikCheck.ABSENT  # nothing printed, or both genders near the block
    return NikCheck.MATCH if coded.female in printed else NikCheck.MISMATCH


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    label_start: int
    options: tuple[str, ...]  # the 16-digit readings of this run


def _candidates(text: str) -> list[_Candidate]:
    """Numbers after an NIK label: on the label's line, or starting the next line (a wrapped sentence)."""
    found: list[_Candidate] = []
    for label in _LABEL.finditer(text):
        line_end = text.find("\n", label.end())
        line_end = len(text) if line_end == -1 else line_end
        next_end = text.find("\n", line_end + 1)
        next_end = len(text) if next_end == -1 else next_end

        def allowed(pos: int, line_end: int = line_end) -> bool:
            return pos < line_end or pos <= line_end + 1 + NEXT_LINE_LEAD

        exact = [m for m in _EXACT.finditer(text, label.end(), next_end) if allowed(m.start())]
        if exact:
            found += [_Candidate(m.start(), m.end(), label.start(), (m.group(0),)) for m in exact]
            continue
        for m in _RUN.finditer(text, label.end(), next_end):
            if not allowed(m.start()) or sum(ch.isdigit() for ch in m.group(0)) < MIN_REAL_DIGITS:
                continue
            digits = _SEPARATORS.sub("", m.group(0)).translate(_SLIPS)
            options = tuple(digits[i : i + NIK_DIGITS] for i in range(len(digits) - NIK_DIGITS + 1))
            found.append(_Candidate(m.start(), m.end(), label.start(), options))
    return found


@dataclass(frozen=True)
class NikSpot:
    """Where the NIK digits are printed: the index into the P_id pages and a box in page fractions."""

    page: int
    box: tuple[float, float, float, float]  # x0, top, x1, bottom
    label_start: int  # in the joined P_id text, for the printed-gender check


def _word_places(pages: list[PageText]) -> list[tuple[int, int, int, tuple[float, float, float, float]]]:
    """Each word's span in the joined text (as `joined_words` builds it), its page index and its box."""
    places = []
    pos = 0
    for i, page in enumerate(pages):
        pos += len(PAGE_SEPARATOR) if i else 0
        for j, line in enumerate(page.content_lines):
            pos += 1 if j else 0
            for k, word in enumerate(line.words):
                pos += 1 if k else 0
                places.append((pos, pos + len(word.text), i, word.bbox))
                pos += len(word.text)
    return places


def _spot(pages: list[PageText], places: list, start: int, end: int, label_start: int) -> NikSpot | None:
    """The padded box around the words of one span, on the page holding most of them."""
    words = [(i, box) for s, e, i, box in places if s < end and start < e]
    if not words:
        return None
    page = max({i for i, _ in words}, key=lambda i: sum(1 for j, _ in words if j == i))
    boxes = [box for i, box in words if i == page]
    x0, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    pad = bottom - top  # one line height sideways, for a digit OCR cut off; a quarter up and down, so the crop
    width, height = pages[page].width, pages[page].height  # stays clear of the lines above and below
    box = (
        max(0.0, (x0 - pad) / width),
        max(0.0, (top - 0.25 * pad) / height),
        min(1.0, (x1 + pad) / width),
        min(1.0, (bottom + 0.25 * pad) / height),
    )
    return NikSpot(page, box, label_start)


def nik_spot(pages: list[PageText]) -> NikSpot | None:
    """Where to crop for a second reading of the NIK: the number that gave the local reading, else the one number
    after a label that could not be read, else the rest of the one label's line. None when there is no label, or
    several numbers that could each be the employee's (a crop cannot tell whose it is)."""
    text, _ = joined_words(pages)
    places = _word_places(pages)
    candidates = _candidates(text)
    readable = [(c, {o for o in c.options if decode(o)}) for c in candidates]
    readable = [(c, valid) for c, valid in readable if valid]
    if readable:  # the same number printed twice is still one; two different ones may be two people
        found, valid = readable[0]
        same = all(other == valid for _, other in readable)
        return _spot(pages, places, found.start, found.end, found.label_start) if same else None
    if len(candidates) == 1:
        found = candidates[0]
        return _spot(pages, places, found.start, found.end, found.label_start)
    if candidates:
        return None
    labels = list(_LABEL.finditer(text))
    if len(labels) != 1:
        return None
    line_end = text.find("\n", labels[0].end())
    line_end = len(text) if line_end == -1 else line_end
    if line_end - labels[0].end() < MIN_REAL_DIGITS:
        return None  # the label ends its line; the number is elsewhere
    return _spot(pages, places, labels[0].end(), line_end, labels[0].start())


def check_nik(nik: str, pages: list[PageText], spot: NikSpot) -> NikLog:
    """The local checks for a NIK read elsewhere (from a crop): structure, printed birth date and printed gender."""
    coded = decode(nik)
    if coded is None:
        return NikLog(status=NikStatus.INVALID)
    text, _ = joined_words(pages)
    return NikLog(
        status=NikStatus.FOUND,
        tanggal_lahir=_birth_check(coded, birth_dates(text)),
        jenis_kelamin=_gender_check(coded, _printed_genders(text, spot.label_start)),
    )


def check_printed(nik: str, birth: date | None, female: bool | None) -> NikLog:
    """The same checks for a NIK read with its printed birth date and gender (the no-OCR mode's page reading)."""
    coded = decode(nik)
    if coded is None:
        return NikLog(status=NikStatus.INVALID)
    return NikLog(
        status=NikStatus.FOUND,
        tanggal_lahir=_birth_check(coded, {birth} if birth else set()),
        jenis_kelamin=_gender_check(coded, set() if female is None else {female}),
    )


def find_nik(pages: list[PageText]) -> NikRead:
    """Read the NIK from the P_id pages; runs locally on the raw text, before masking."""
    text, spans = joined_words(pages)
    births = birth_dates(text)
    readings: dict[str, _Candidate] = {}
    unreadable = False
    for cand in _candidates(text):
        valid = [option for option in dict.fromkeys(cand.options) if decode(option)]
        if len(valid) > 1:
            # A long run with several valid windows: keep the ones the printed birth date and gender agree with.
            printed = _printed_genders(text, cand.label_start)
            valid = [
                v
                for v in valid
                if _birth_check(decode(v), births) is not NikCheck.MISMATCH  # type: ignore[arg-type]
                and _gender_check(decode(v), printed) is not NikCheck.MISMATCH  # type: ignore[arg-type]
            ]
        if len(valid) == 1:
            readings.setdefault(valid[0], cand)
        else:
            unreadable = True

    if len(readings) > 1:
        return NikRead(None, NikLog(status=NikStatus.CONFLICT), None)
    if not readings:
        return NikRead(None, NikLog(status=NikStatus.INVALID if unreadable else NikStatus.MISSING), None)

    nik, cand = next(iter(readings.items()))
    coded = decode(nik)
    assert coded is not None
    log = NikLog(
        status=NikStatus.FOUND,
        tanggal_lahir=_birth_check(coded, births),
        jenis_kelamin=_gender_check(coded, _printed_genders(text, cand.label_start)),
        deret_panjang=len(cand.options) > 1,
    )
    confidences = [conf for start, end, conf in spans if start < cand.end and cand.start < end]
    return NikRead(nik, log, min(confidences, default=1.0))
