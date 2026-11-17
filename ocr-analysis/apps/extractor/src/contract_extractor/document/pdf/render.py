"""Render halaman terpilih ke citra grayscale di memori. Citra tidak pernah ditulis ke disk oleh modul ini."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType

import numpy as np
import pypdfium2 as pdfium

from contract_extractor.document.pdf.source import PdfSource


class PageRenderer:
    def __init__(self, source: PdfSource, turns: Mapping[int, int] | None = None) -> None:
        self._pdf = pdfium.PdfDocument(source if isinstance(source, bytes) else str(source))
        # Page -> clockwise degrees (90, 180, 270) that make a photo taken sideways or upside down upright.
        self.turns: dict[int, int] = dict(turns or {})

    def render_gray(self, page_number: int, dpi: int, turn: int | None = None) -> np.ndarray:
        """1-based page to a 2-D uint8 array; annotations are drawn because signatures may live there. The page is
        turned upright by its entry in `turns`, or by `turn` when given."""
        page = self._pdf[page_number - 1]
        rotation = self.turns.get(page_number, 0) if turn is None else turn
        try:
            bitmap = page.render(scale=dpi / 72, grayscale=True, draw_annots=True, rotation=rotation)
            array = np.array(bitmap.to_numpy(), dtype=np.uint8, copy=True)
        finally:
            page.close()
        return array[:, :, 0] if array.ndim == 3 else array

    def render_rgb(self, page_number: int, dpi: int) -> np.ndarray:
        """1-based page to an (h, w, 3) RGB array, the same size and turn as render_gray at this dpi; colour tells
        a meterai from pen ink on the signature page."""
        page = self._pdf[page_number - 1]
        rotation = self.turns.get(page_number, 0)
        try:
            bitmap = page.render(scale=dpi / 72, draw_annots=True, rev_byteorder=True, rotation=rotation)
            array = np.array(bitmap.to_numpy(), dtype=np.uint8, copy=True)
        finally:
            page.close()
        return array[:, :, :3]

    def close(self) -> None:
        self._pdf.close()

    def __enter__(self) -> PageRenderer:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()
