"""Render halaman terpilih ke citra grayscale di memori. Citra tidak pernah ditulis ke disk oleh modul ini."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

import numpy as np
import pypdfium2 as pdfium


class PageRenderer:
    def __init__(self, path: Path) -> None:
        self._pdf = pdfium.PdfDocument(str(path))

    def render_gray(self, page_number: int, dpi: int) -> np.ndarray:
        """1-based page to a 2-D uint8 array; annotations are drawn because signatures may live there."""
        page = self._pdf[page_number - 1]
        try:
            bitmap = page.render(scale=dpi / 72, grayscale=True, draw_annots=True)
            array = np.array(bitmap.to_numpy(), dtype=np.uint8, copy=True)
        finally:
            page.close()
        return array[:, :, 0] if array.ndim == 3 else array

    def close(self) -> None:
        self._pdf.close()

    def __enter__(self) -> PageRenderer:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()
