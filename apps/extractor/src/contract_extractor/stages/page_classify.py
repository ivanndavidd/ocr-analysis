"""Tahap 2a: klasifikasi halaman native, scan, atau hybrid (Bagian 9 PRD)."""

from __future__ import annotations

from dataclasses import dataclass

from contract_extractor.models import PageKind

MIN_TEXT_CHARS = 50
DOMINANT_IMAGE_COVERAGE = 0.70


@dataclass(frozen=True)
class PageStats:
    number: int  # 1-based
    n_chars: int
    max_image_coverage: float  # largest single image area / page area


def classify_page(stats: PageStats) -> PageKind:
    """Text-bbox coverage is deliberately unused: native sample pages cover only 8-37%."""
    if stats.max_image_coverage > DOMINANT_IMAGE_COVERAGE:
        return PageKind.HYBRID if stats.n_chars >= MIN_TEXT_CHARS else PageKind.SCAN
    if stats.n_chars >= MIN_TEXT_CHARS:
        return PageKind.NATIVE
    return PageKind.UNCLASSIFIED
