"""Tahap 1: memilih P_id dan P_ttd secara lokal dari skor kata kunci dan zona penutup (Bagian 8 PRD)."""

from __future__ import annotations

from dataclasses import dataclass, field

from contract_extractor.models import Word


@dataclass(frozen=True)
class PageSelection:
    p_id: list[int]  # 1-based, at most two pages
    p_ttd: int | None
    sensitive_pages: list[int] = field(default_factory=list)
    best_scores: dict[str, float] = field(default_factory=dict)


def select_pages(pages: list[list[Word]], filename_name: str | None) -> PageSelection:
    """Fase 1: implement keyword scoring, closing-zone detection, and threshold calibration."""
    raise NotImplementedError("page_selection")
