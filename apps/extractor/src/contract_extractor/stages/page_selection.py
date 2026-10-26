"""Tahap 1: memilih P_id dan P_ttd secara lokal dari skor kata kunci dan zona penutup (Bagian 8 PRD).

Skor memakai kehadiran kata kunci, bukan frekuensinya, karena `Pihak Pertama`/`Pihak Kedua` muncul
berulang di isi pasal. Bobot dan ambang di bawah adalah nilai awal yang dikalibrasi di test set Fase 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from contract_extractor.dates import MONTHS
from contract_extractor.layout import Line, PageText
from contract_extractor.models import Level
from contract_extractor.stages.filename import name_in_text, normalize_name
from contract_extractor.stages.masking import has_long_digit_run
from contract_extractor.stages.scoring import page_selection_level, worst

ID_CANDIDATES = 3  # P_id is searched in the first pages only
TTD_CANDIDATES = 5  # P_ttd is searched in the last pages only

IDENTITY_THRESHOLD = 6.0  # a body page with party names scores ~3-5; the identity page ~10+
PERIOD_THRESHOLD = 3.0
SIGNATURE_THRESHOLD = 5.0  # a signature page without a name match still reaches 6
LEVEL_MARGIN = 3.0

BODY_LINE_MIN_WORDS = 6
BODY_LINE_MIN_WIDTH = 0.5  # fraction of the page's text span
LARGE_EMPTY_ZONE = 0.2  # fraction of page height


def _kw(*phrases: str) -> re.Pattern[str]:
    alternatives = "|".join(r"\s+".join(re.escape(tok) for tok in p.split()) for p in phrases)
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])", re.IGNORECASE)


_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_DATE = re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTH_ALT})\.?\s+\d{{4}}\b|\b\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}}\b", re.IGNORECASE)
_BIRTH = re.compile(r"(?:tempat\s*(?:/|,|dan)\s*)?(?:tgl\.?|tanggal)\s*lahir", re.IGNORECASE)

_IDENTITY_KEYWORDS: list[tuple[re.Pattern[str], float]] = [
    (_kw("perjanjian kerja"), 1),
    (_kw("pkwt", "pkwtt", "perjanjian kerja waktu tertentu"), 2),
    (_kw("nama"), 1),
    (_kw("nik", "no. ktp", "nomor ktp"), 1),
    (_BIRTH, 1),
    (_kw("jenis kelamin"), 1),
    (_kw("selanjutnya disebut"), 1),
    (_kw("tenaga kerja"), 1),
    (_kw("pihak kedua"), 1),
    (_kw("para pihak"), 1),
]
_NAME_LABEL = re.compile(r"^\s*nama\s*:", re.IGNORECASE)
_NAME_BLOCK_FOLLOWERS = re.compile(r"(?<![a-z])(?:nik|ktp)(?![a-z])|lahir", re.IGNORECASE)
NAME_BLOCK_BONUS = 3.0
NAME_ON_IDENTITY_BONUS = 1.0

_PERIOD_KEYWORDS: list[tuple[re.Pattern[str], float]] = [
    (_kw("jangka waktu"), 1),
    (_kw("berlaku efektif"), 1),
    (_kw("dimulai sejak", "dimulai pada"), 1),
    (_kw("berakhir dengan sendirinya", "berakhir pada"), 1),
    (_kw("sampai dengan tanggal"), 1),
    (re.compile(r"\bs/d\b", re.IGNORECASE), 1),
]
TWO_DATES_BONUS = 3.0

_CLOSING = _kw("demikian perjanjian", "demikian perjanjian kerja", "dibuat dan ditandatangani")
_ROLE_PAIRS = [
    (_kw("pihak pertama"), _kw("pihak kedua")),
    (_kw("pengusaha", "perusahaan", "pt"), _kw("tenaga kerja", "pekerja", "karyawan")),
]
ROLE_PAIR_BONUS = 3.0
_SIGNING_WORDS = [_kw("tanda tangan", "ditandatangani"), _kw("menyetujui", "disetujui"), _kw("materai", "meterai")]
_PLACE_DATE = re.compile(rf"^[a-z .]{{3,30}},\s*\d{{1,2}}\s+(?:{_MONTH_ALT})\.?\s+\d{{4}}", re.IGNORECASE)
PLACE_DATE_BONUS = 2.0
EMPTY_ZONE_BONUS = 1.0
NAME_IN_ZONE_BONUS = 4.0

_SENSITIVE = _kw("gaji", "upah", "tunjangan", "thr", "bonus", "bpjs", "potongan", "rekening")
_RUPIAH = re.compile(r"\b(?:rp|idr)\.?\s*\d", re.IGNORECASE)


@dataclass(frozen=True)
class PageScores:
    identity: float
    period: float
    signature: float
    sensitive: float

    def as_dict(self) -> dict[str, float]:
        return {
            "identitas": float(self.identity),
            "periode": float(self.period),
            "ttd": float(self.signature),
            "sensitif": float(self.sensitive),
        }


@dataclass(frozen=True)
class PageSelection:
    p_id: list[int]
    p_ttd: int | None
    level: Level
    sensitive_pages: list[int] = field(default_factory=list)
    scores: dict[int, PageScores] = field(default_factory=dict)

    @property
    def ambiguous(self) -> bool:
        return not self.p_id or self.p_ttd is None


def candidate_pages(n_pages: int) -> list[int]:
    """Pages that can ever become P_id or P_ttd; economy mode reads only these."""
    first = range(1, min(ID_CANDIDATES, n_pages) + 1)
    last = range(max(1, n_pages - TTD_CANDIDATES + 1), n_pages + 1)
    return sorted(set(first) | set(last))


def _presence(patterns: list[tuple[re.Pattern[str], float]], text: str) -> float:
    return sum(weight for pattern, weight in patterns if pattern.search(text))


def closing_zone(page: PageText) -> list[Line]:
    """Lines from the closing sentence (or after the last body paragraph) to the page bottom."""
    lines = page.lines
    if not lines:
        return []
    span = (max(l.x1 for l in lines) - min(l.x0 for l in lines)) or 1.0
    body = [l for l in lines if len(l.words) >= BODY_LINE_MIN_WORDS and (l.x1 - l.x0) / span >= BODY_LINE_MIN_WIDTH]
    if not body:
        return lines
    zone_top = max(l.bottom for l in body)
    # A closing sentence that wraps over the last body lines opens the zone at its first line.
    closing_tops = [l.top for l in body[-2:] if _CLOSING.search(l.text)]
    if closing_tops:
        zone_top = min(closing_tops)
    return [l for l in lines if l.top >= zone_top - 0.5]


def _identity_score(page: PageText, name_norm: str) -> float:
    text = page.text
    score = _presence(_IDENTITY_KEYWORDS, text)
    lines = page.lines
    for i, line in enumerate(lines):
        if _NAME_LABEL.search(line.text) and any(_NAME_BLOCK_FOLLOWERS.search(l.text) for l in lines[i + 1 : i + 5]):
            score += NAME_BLOCK_BONUS
            break
    if any(name_in_text(name_norm, line.text) for line in lines):
        score += NAME_ON_IDENTITY_BONUS
    return score


def _period_score(page: PageText) -> float:
    score = _presence(_PERIOD_KEYWORDS, page.text)
    lines = [l.text for l in page.lines]
    # Sentences wrap, so look at each line joined with the next one.
    windows = [" ".join(lines[i : i + 2]) for i in range(len(lines))]
    if any(len(_DATE.findall(w)) >= 2 for w in windows):
        score += TWO_DATES_BONUS
    return score


def _signature_score(page: PageText, name_norm: str) -> float:
    zone = closing_zone(page)
    if not zone:
        return 0.0
    zone_text = "\n".join(l.text for l in zone)
    score = 0.0
    if any(a.search(zone_text) and b.search(zone_text) for a, b in _ROLE_PAIRS):
        score += ROLE_PAIR_BONUS
    score += sum(1.0 for pattern in _SIGNING_WORDS if pattern.search(zone_text))
    if any(_PLACE_DATE.search(l.text) for l in zone):
        score += PLACE_DATE_BONUS
    if (page.height - zone[0].top) / page.height >= LARGE_EMPTY_ZONE:
        score += EMPTY_ZONE_BONUS
    if any(name_in_text(name_norm, l.text) for l in zone):
        score += NAME_IN_ZONE_BONUS
    return score


def _sensitive_score(page: PageText) -> float:
    text = page.text
    return float(bool(_SENSITIVE.search(text))) + float(bool(_RUPIAH.search(text))) + float(has_long_digit_run(text))


def score_page(page: PageText, name_norm: str) -> PageScores:
    return PageScores(
        identity=_identity_score(page, name_norm),
        period=_period_score(page),
        signature=_signature_score(page, name_norm),
        sensitive=_sensitive_score(page),
    )


def select_pages(pages: list[PageText], n_pages: int, employee_name: str | None) -> PageSelection:
    """`pages` holds the pages with text (native now, OCR later); missing pages simply cannot win."""
    name_norm = normalize_name(employee_name) if employee_name else ""
    scores = {p.number: score_page(p, name_norm) for p in pages}

    id_candidates = [n for n in sorted(scores) if n <= ID_CANDIDATES]

    # Ties: earliest page for identity and period, latest page for the signature.
    best_id = max(id_candidates, key=lambda n: (scores[n].identity, -n), default=None)
    best_period = max(id_candidates, key=lambda n: (scores[n].period, -n), default=None)
    id_score = scores[best_id].identity if best_id is not None else 0.0

    p_id: list[int] = []
    if best_id is not None and id_score >= IDENTITY_THRESHOLD:
        p_id.append(best_id)
        if (
            best_period is not None
            and best_period != best_id
            and scores[best_period].period >= PERIOD_THRESHOLD
            and scores[best_period].period > scores[best_id].period
        ):
            p_id.append(best_period)

    # The identity page carries both party labels and the employee name, so on a short page its closing
    # zone can look like a signature block. It may only be P_ttd in a one-page contract.
    ttd_candidates = [
        n for n in sorted(scores) if n > n_pages - TTD_CANDIDATES and (n_pages == 1 or not p_id or n != p_id[0])
    ]
    best_ttd = max(ttd_candidates, key=lambda n: (scores[n].signature, n), default=None)
    ttd_score = scores[best_ttd].signature if best_ttd is not None else 0.0
    p_ttd = best_ttd if best_ttd is not None and ttd_score >= SIGNATURE_THRESHOLD else None

    level = worst(
        page_selection_level(id_score, IDENTITY_THRESHOLD, LEVEL_MARGIN),
        page_selection_level(ttd_score, SIGNATURE_THRESHOLD, LEVEL_MARGIN),
    )
    return PageSelection(
        p_id=sorted(p_id),
        p_ttd=p_ttd,
        level=level,
        sensitive_pages=[n for n in sorted(scores) if scores[n].sensitive > 0],
        scores=scores,
    )
