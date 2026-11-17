"""Tahap 5b: level hijau/kuning/merah per komponen dan keputusan per file (Bagian 12 PRD).

Aturannya sengaja sederhana agar bisa dijelaskan ke HR; ambang dikalibrasi di test set.
"""

from __future__ import annotations

from collections.abc import Mapping

from contract_extractor.common.models import Decision, Level, NikCheck, NikLog, NikStatus, SignatureStatus
from contract_extractor.extractor_method.pattern.filename import FLAG_NONSTANDARD, FLAG_UNPARSED, Match

FLAG_FILENAME_MISMATCH = "filename_mismatch"

COMPONENT_FILENAME = "validasi_nama_file"


_SEVERITY = {Level.GREEN: 0, Level.YELLOW: 1, Level.RED: 2}


def worst(*levels: Level) -> Level:
    return max(levels, key=_SEVERITY.__getitem__)


def page_selection_level(score: float, threshold: float, margin: float) -> Level:
    if score < threshold:
        return Level.RED
    return Level.GREEN if score >= threshold + margin else Level.YELLOW


def ocr_level(mean_conf: float) -> Level:
    """mean_conf in 0..1; native text layers count as 1.0."""
    if mean_conf >= 0.85:
        return Level.GREEN
    return Level.YELLOW if mean_conf >= 0.70 else Level.RED


def filename_level(name: Match, period: Match | None, flags: tuple[str, ...]) -> Level:
    if name is Match.TIDAK_COCOK or period is Match.TIDAK_COCOK:
        return Level.RED
    if FLAG_NONSTANDARD in flags or FLAG_UNPARSED in flags:
        return Level.YELLOW
    if name is Match.MIRIP or period is Match.MIRIP or period is None:
        return Level.YELLOW
    return Level.GREEN


def nik_level(log: NikLog, from_text_layer: bool) -> Level:
    """Red for two different NIKs or a NIK the printed birth date or gender contradicts; yellow when there is none to
    report. Green only from a text layer: one misread OCR digit (say the province code) still gives a valid NIK."""
    if log.status is NikStatus.CONFLICT or NikCheck.MISMATCH in (log.tanggal_lahir, log.jenis_kelamin):
        return Level.RED
    if log.status is NikStatus.FOUND and from_text_layer:
        return Level.GREEN
    return Level.YELLOW


def signature_level(status: SignatureStatus, anchor_found: bool, clear_margin: bool) -> Level:
    if not anchor_found:
        return Level.RED
    if status is SignatureStatus.UNCERTAIN or not clear_margin:
        return Level.YELLOW
    return Level.GREEN


def decide(levels: Mapping[str, Level]) -> tuple[Decision, list[str]]:
    """All green → auto; any red → review_priority; otherwise review. Empty input never auto-passes."""
    flags: list[str] = []
    values = list(levels.values())
    if not values:
        return Decision.REVIEW_PRIORITY, flags

    others = [lvl for name, lvl in levels.items() if name != COMPONENT_FILENAME]
    if levels.get(COMPONENT_FILENAME) is Level.RED and others and all(lvl is Level.GREEN for lvl in others):
        flags.append(FLAG_FILENAME_MISMATCH)

    if Level.RED in values:
        return Decision.REVIEW_PRIORITY, flags
    if Level.YELLOW in values:
        return Decision.REVIEW, flags
    return Decision.AUTO, flags
