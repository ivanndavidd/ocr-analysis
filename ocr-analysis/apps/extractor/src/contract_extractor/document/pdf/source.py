"""Sumber PDF: path file di disk, atau byte yang sudah ada di memori (dari website). Byte tidak pernah ditulis ke disk."""

from __future__ import annotations

import io
from pathlib import Path

import pdfplumber

PdfSource = Path | bytes


def open_pdf(source: PdfSource) -> pdfplumber.PDF:
    return pdfplumber.open(io.BytesIO(source) if isinstance(source, bytes) else source)
