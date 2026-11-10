"""Tahap 3, jalur lokal: jabatan (posisi) karyawan dari teks P_id mentah.

Dua tempat yang dibaca: baris `Jabatan :` atau `Posisi :` di blok identitas karyawan, yaitu blok baris `label : isi`
yang juga memuat NIK, tempat/tanggal lahir, atau jenis kelamin; dan kalimat penempatan seperti `mempekerjakan PIHAK
KEDUA sebagai Kasir`. Jabatan perwakilan perusahaan tidak pernah diambil: bloknya (`Nama`, `Jabatan`, `Alamat`) tidak
memuat data pribadi, dan kalimatnya memakai `selaku`. Dua jabatan yang berbeda membuat hasilnya null.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from contract_extractor.common.layout import PageText
from contract_extractor.common.models import PositionLog, PositionSource, PositionStatus
from contract_extractor.extractor_method.pattern.nik import joined_words

MAX_CHARS = 60
SAME_POSITION = 90  # token_set_ratio; `Kasir` and `KASIR`, or `Staff Gudang` and `Staff Gudang Cikarang`, are one
BLOCK_GAP = 2  # unlabelled lines (a wrapped address) may sit inside an identity block

# OCR puts debris before a row (`: Jenis Kelamin : Pria`, `| Posisi Kerja : Kurir`); a numbered clause stays prose.
_ROW = re.compile(r"^[\s:;|.,'\"!*>_-]*(?P<label>[A-Za-z][\w ./,()&|!-]{0,40}?)\s*[:;：]\s*(?P<value>\S.*)$")
# `Jabatan`, `Posisi`, `Jabatan/Posisi`, `Posisi Kerja`; OCR may leave one or two letters of debris before it.
_POSITION_LABEL = re.compile(
    r"^(?:[a-z]{1,2}\s+)?(?:jabatan|posisi)(?:\s*[/|]\s*(?:jabatan|posisi))?(?:\s+kerja)?$", re.IGNORECASE
)
# Each party's block opens with a name row; OCR reads `Nama` as `Hama`, `Hema` or `Narna` too.
_NAME_LABEL = re.compile(r"^[nhm][ae](?:m|rn|nn|n)a(?:\s+(?:lengkap|karyawan|pekerja))?$", re.IGNORECASE)
# An unlabelled line that is prose, not a wrapped value: the company block ends `bertindak untuk dan atas nama ...`.
_PROSE = re.compile(r"\b(?:bertindak|disebut|selanjutnya|perseroan|pihak)\b", re.IGNORECASE)
# Rows only the employee's block has: the NIK, birth place and date, gender.
_PERSONAL_LABEL = re.compile(r"n[i1l|!]k\b|ktp|induk\s+kependudukan|lahir|ttl|jenis\s+kelamin", re.IGNORECASE)
# An unlabelled line holding the NIK: OCR read the row's colon as something else (`No. KTP/NIK 1 3201...`).
_NIK_DIGITS = re.compile(r"\d{12,}")
# The words that end a position in a sentence; OCR may glue the next word on with a dash (`Merchandiser-dengan`).
_VALUE = rf"(?P<{{name}}>[^,.;:()\"“”]{{{{2,{MAX_CHARS}}}}}?)(?=[\s\-–—]+(?:yang|dengan|di|pada|untuk|dimulai|terhitung|sejak|selama|dan|sesuai|ditempatkan)\b|\s*[,.;:()\"“”]|$)"
_SENTENCE = re.compile(
    # never `selaku` or `bertindak sebagai`: that is how the company's signatory is introduced
    r"\b(?:mempekerjakan|memberikan\s+pekerjaan\s+kepada|mengangkat|menerima)\s+(?:\S+\s+){1,3}?sebagai\s+"
    + _VALUE.format(name="hired")
    # `Dalam jabatannya sebagai Kurir, PEKERJA wajib ...`, `... atas pelaksanaan pekerjaan sebagai Kurir sesuai ...`
    + r"|\b(?:dipekerjakan|bekerja|tugas|ditempatkan|jabatannya|pekerjaan)\s+sebagai\s+"
    + _VALUE.format(name="works")
    + r"|\b(?:dengan|pada)\s+(?:jabatan|posisi)\s+(?:sebagai\s+)?"
    + _VALUE.format(name="titled"),
    re.IGNORECASE,
)
# What follows `sebagai` without being a position: the employment status, or `sebagai berikut`.
_NOT_POSITION = re.compile(
    r"^(?:berikut|tersebut|(?:karyawan|pekerja|pegawai|tenaga\s+kerja)"
    r"(?:\s+(?:kontrak|tetap|harian|lepas|waktu\s+tertentu|pkwt|perusahaan))*)$",
    re.IGNORECASE,
)
# A title naming a company is the signatory's (`HR Manager PT Contoh`), not an employee's.
_COMPANY = re.compile(r"\b(?:PT|CV|Tbk|Persero)\b", re.IGNORECASE)
# A sentence followed by this introduces the company's signatory (`dalam jabatannya sebagai Direktur, bertindak
# untuk dan atas nama PT ...`), not the employee.
_SIGNATORY_AFTER = re.compile(r".{0,40}?\b(?:bertindak|mewakili|atas\s+nama)\b", re.IGNORECASE | re.DOTALL)  # .match
# A token with no letter or digit inside a title is OCR debris (`Live Chat ' | Agent`); `-`, `&` and `/` are kept.
_DEBRIS_TOKEN = re.compile(r"^[^\w&/\-–]+$")
# OCR reads Q as O: `Staff OC/Packing`, `Ouality Control`.
_Q_AS_O = ((re.compile(r"\bOC\b"), "QC"), (re.compile(r"\bOual"), "Qual"), (re.compile(r"\bOUAL"), "QUAL"))
# One or two letters, not an acronym, after a title read by OCR are debris from the page (`Kurir Motor Ta`).
_TRAILING_DEBRIS = re.compile(r"\s+(?![A-Z]{2}$)[A-Za-z]{1,2}$")


@dataclass(frozen=True)
class PositionRead:
    posisi: str | None
    log: PositionLog
    confidence: float | None  # lowest OCR word confidence over the position (1.0 from a text layer)


@dataclass(frozen=True)
class _Candidate:
    value: str
    source: PositionSource
    start: int
    end: int


def _clean(value: str, ocr: bool = False) -> str | None:
    value = " ".join(t for t in value.split() if not _DEBRIS_TOKEN.match(t))
    value = value.strip(" *_-–—'\"“”:;|.,!")  # OCR junk such as `Kurir |`
    if ocr:
        for pattern, fixed in _Q_AS_O:
            value = pattern.sub(fixed, value)
        if len(value.split()) >= 2:
            value = _TRAILING_DEBRIS.sub("", value)
    if not 2 <= len(value) <= MAX_CHARS or sum(ch.isalpha() for ch in value) < 2:
        return None
    if _NOT_POSITION.match(value) or _COMPANY.search(value):
        return None
    return value


def _from_identity_blocks(text: str, ocr: bool) -> list[_Candidate]:
    """`Jabatan : X` rows in a block of `label : value` rows that also holds the employee's personal rows.

    A block runs from a `Nama` row through consecutive rows, allowing up to two unlabelled lines (a wrapped address,
    or the NIK row whose colon OCR misread) but no prose line.
    """
    found: list[_Candidate] = []
    lines: list[tuple[int, str]] = []
    pos = 0
    for line in text.split("\n"):
        lines.append((pos, line))
        pos += len(line) + 1

    block: list[tuple[int, re.Match[str]]] = []
    gap = 0
    nik_line = False  # an unlabelled line of the block holds the NIK

    def close() -> None:
        nonlocal nik_line
        if nik_line or any(_PERSONAL_LABEL.search(m.group("label")) for _, m in block):
            for start, m in block:
                if _POSITION_LABEL.match(m.group("label").strip()) and (value := _clean(m.group("value"), ocr)):
                    offset = start + m.start("value")
                    found.append(_Candidate(value, PositionSource.LABEL, offset, offset + len(m.group("value"))))
        block.clear()
        nik_line = False

    for start, line in lines:
        match = _ROW.match(line)
        if match:
            if _NAME_LABEL.match(match.group("label").strip()):
                close()  # the company's `Nama`/`Jabatan` rows may sit one line above the employee's
            block.append((start, match))
            gap = 0
        elif block and gap < BLOCK_GAP and line.strip() and not _PROSE.search(line):
            gap += 1
            nik_line = nik_line or bool(_PERSONAL_LABEL.search(line) and _NIK_DIGITS.search(line.replace(" ", "")))
        else:
            close()
            gap = 0
    close()
    return found


def _from_sentences(text: str, ocr: bool) -> list[_Candidate]:
    flat = text.replace("\n", " ")  # same length, so offsets still point into `text`
    found = []
    for m in _SENTENCE.finditer(flat):
        group = next(g for g in ("hired", "works", "titled") if m.group(g))
        if _SIGNATORY_AFTER.match(flat, m.end(group)):
            continue
        if value := _clean(m.group(group), ocr):
            found.append(_Candidate(value, PositionSource.SENTENCE, m.start(group), m.end(group)))
    return found


def find_position(pages: list[PageText]) -> PositionRead:
    """Read the employee's position from the P_id pages, locally."""
    text, spans = joined_words(pages)
    ocr = any(page.source.startswith("ocr:") for page in pages)  # OCR's own slips are cleaned only on OCR text
    candidates = _from_identity_blocks(text, ocr) + _from_sentences(text, ocr)
    if not candidates:
        return PositionRead(None, PositionLog(status=PositionStatus.MISSING), None)

    groups: list[list[_Candidate]] = []
    for cand in candidates:
        for group in groups:
            if fuzz.token_set_ratio(cand.value.casefold(), group[0].value.casefold()) >= SAME_POSITION:
                group.append(cand)
                break
        else:
            groups.append([cand])
    if len(groups) > 1:
        return PositionRead(None, PositionLog(status=PositionStatus.CONFLICT), None)

    # The identity row is the position as the contract states it; a sentence may add the placement.
    best = min(groups[0], key=lambda c: (c.source is not PositionSource.LABEL, len(c.value)))
    confidences = [conf for start, end, conf in spans if start < best.end and best.start < end]
    return PositionRead(
        best.value,
        PositionLog(status=PositionStatus.FOUND, sumber=best.source),
        min(confidences, default=1.0),
    )
