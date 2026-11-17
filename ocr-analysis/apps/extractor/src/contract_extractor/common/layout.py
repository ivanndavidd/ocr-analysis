"""Teks halaman berkoordinat dan pengelompokan kata menjadi baris, terlepas dari sumbernya (text layer atau OCR)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from statistics import median

from contract_extractor.common.models import Word

# Words whose vertical centres differ by less than this fraction of the median word height share a line.
_LINE_TOLERANCE = 0.5
_LETTER_OR_DIGIT = re.compile(r"[^\W_]")


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def is_debris(self) -> bool:
        """Not one letter or digit (`|`, `| |`, `�`): a scrap of a table border or of the page edge read as a line."""
        return not _LETTER_OR_DIGIT.search(self.text)

    @property
    def x0(self) -> float:
        return min(w.bbox[0] for w in self.words)

    @property
    def top(self) -> float:
        return min(w.bbox[1] for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.bbox[2] for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bbox[3] for w in self.words)


@dataclass(frozen=True)
class PageText:
    """Words of one page; bbox units match width/height (PDF points for text layers, pixels for OCR)."""

    number: int  # 1-based
    width: float
    height: float
    words: tuple[Word, ...]
    source: str  # "text_layer" or "ocr:<engine>"

    @cached_property
    def lines(self) -> list[Line]:
        return group_lines(self.words)

    @cached_property
    def content_lines(self) -> list[Line]:
        """`lines` without debris lines, for reading text; layout checks keep every line. On scans and photos OCR reads
        a table border or the page edge as lines of `|` between text lines, which split a phrase (`sampai` / `|` /
        `dengan`: 3 period conflicts in 184 synthetic scans and photos) and push a value further from its label (the
        NIK under `pemegang NIK`: 2 NIKs not found)."""
        return [line for line in self.lines if not line.is_debris]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.content_lines)


def group_lines(words: tuple[Word, ...] | list[Word]) -> list[Line]:
    if not words:
        return []
    tolerance = _LINE_TOLERANCE * median(w.bbox[3] - w.bbox[1] for w in words)

    def centre(w: Word) -> float:
        return (w.bbox[1] + w.bbox[3]) / 2

    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (centre(w), w.bbox[0])):
        if rows and abs(centre(word) - sum(centre(w) for w in rows[-1]) / len(rows[-1])) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [Line(tuple(sorted(row, key=lambda w: w.bbox[0]))) for row in rows]
