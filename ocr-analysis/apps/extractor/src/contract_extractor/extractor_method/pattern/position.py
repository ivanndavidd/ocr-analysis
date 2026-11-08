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

_ROW = re.compile(r"^\s*(?P<label>[A-Za-z][\w ./,()&|!-]{0,40}?)\s*[:;：]\s*(?P<value>\S.*)$")
_POSITION_LABEL = re.compile(r"^(?:jabatan|posisi)$", re.IGNORECASE)
# Each party's block opens with a name row; OCR reads `Nama` as `Hama`, `Hema` or `Narna` too.
_NAME_LABEL = re.compile(r"^[nhm][ae](?:m|rn|nn|n)a(?:\s+(?:lengkap|karyawan|pekerja))?$", re.IGNORECASE)
# An unlabelled line that is prose, not a wrapped value: the company block ends `bertindak untuk dan atas nama ...`.
_PROSE = re.compile(r"\b(?:bertindak|disebut|selanjutnya|perseroan|pihak)\b", re.IGNORECASE)
# Rows only the employee's block has: the NIK, birth place and date, gender.
_PERSONAL_LABEL = re.compile(r"n[i1l|!]k\b|ktp|induk\s+kependudukan|lahir|ttl|jenis\s+kelamin", re.IGNORECASE)
_VALUE = rf"(?P<{{name}}>[^,.;:()\"“”]{{{{2,{MAX_CHARS}}}}}?)(?=\s+(?:yang|dengan|di|pada|untuk|dimulai|terhitung|sejak|selama|dan)\b|\s*[,.;:()\"“”]|$)"
_SENTENCE = re.compile(
    # never `selaku` or `bertindak sebagai`: that is how the company's signatory is introduced
    r"\b(?:mempekerjakan|memberikan\s+pekerjaan\s+kepada|mengangkat|menerima)\s+(?:\S+\s+){1,3}?sebagai\s+"
    + _VALUE.format(name="hired")
    + r"|\b(?:dipekerjakan|bekerja|tugas|ditempatkan)\s+sebagai\s+"
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


def _clean(value: str) -> str | None:
    value = " ".join(value.split()).strip(" *_-–—'\"“”:;|.,!")  # OCR junk such as `Kurir |`
    if not 2 <= len(value) <= MAX_CHARS or sum(ch.isalpha() for ch in value) < 2:
        return None
    if _NOT_POSITION.match(value) or _COMPANY.search(value):
        return None
    return value


def _from_identity_blocks(text: str) -> list[_Candidate]:
    """`Jabatan : X` rows in a block of `label : value` rows that also holds the employee's personal rows.

    A block runs from a `Nama` row through consecutive rows, allowing up to two unlabelled lines (a wrapped address)
    but no prose line.
    """
    found: list[_Candidate] = []
    lines: list[tuple[int, str]] = []
    pos = 0
    for line in text.split("\n"):
        lines.append((pos, line))
        pos += len(line) + 1

    block: list[tuple[int, re.Match[str]]] = []
    gap = 0

    def close() -> None:
        if any(_PERSONAL_LABEL.search(m.group("label")) for _, m in block):
            for start, m in block:
                if _POSITION_LABEL.match(m.group("label").strip()) and (value := _clean(m.group("value"))):
                    offset = start + m.start("value")
                    found.append(_Candidate(value, PositionSource.LABEL, offset, offset + len(m.group("value"))))
        block.clear()

    for start, line in lines:
        match = _ROW.match(line)
        if match:
            if _NAME_LABEL.match(match.group("label").strip()):
                close()  # the company's `Nama`/`Jabatan` rows may sit one line above the employee's
            block.append((start, match))
            gap = 0
        elif block and gap < BLOCK_GAP and line.strip() and not _PROSE.search(line):
            gap += 1
        else:
            close()
            gap = 0
    close()
    return found


def _from_sentences(text: str) -> list[_Candidate]:
    flat = text.replace("\n", " ")  # same length, so offsets still point into `text`
    found = []
    for m in _SENTENCE.finditer(flat):
        group = next(g for g in ("hired", "works", "titled") if m.group(g))
        if value := _clean(m.group(group)):
            found.append(_Candidate(value, PositionSource.SENTENCE, m.start(group), m.end(group)))
    return found


def find_position(pages: list[PageText]) -> PositionRead:
    """Read the employee's position from the P_id pages, locally."""
    text, spans = joined_words(pages)
    candidates = _from_identity_blocks(text) + _from_sentences(text)
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
