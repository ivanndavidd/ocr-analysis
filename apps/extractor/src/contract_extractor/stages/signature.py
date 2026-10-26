"""Tahap 4: deteksi tanda tangan karyawan, sepenuhnya lokal (Bagian 11 PRD)."""

from __future__ import annotations

from dataclasses import dataclass

from contract_extractor.models import PageKind, SignatureStatus


@dataclass(frozen=True)
class SignatureResult:
    status: SignatureStatus
    source: str | None  # digital | annotation | image | vector | pixels
    anchor_found: bool
    clear_margin: bool


def detect_signature(pdf_path: str, page_number: int, kind: PageKind, employee_name: str | None) -> SignatureResult:
    """Fase 3: native object checks (sig fields, annotations, images, vectors) plus ROI ink analysis."""
    raise NotImplementedError("signature")
