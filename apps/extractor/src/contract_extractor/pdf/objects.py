"""Objek halaman yang bisa menjadi tanda tangan di PDF native (Bagian 11 PRD, jalur native langkah 1-4).

Semua koordinat dalam satuan halaman pdfplumber (point, y dari atas), sama dengan kata dari text layer.
pdfplumber sudah membaca anotasi, widget field tanda tangan, gambar, dan path vektor, jadi pypdf tidak dibutuhkan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdftypes import resolve1

# Annotation types that signing apps use: Acrobat Fill & Sign, iOS Markup (Stamp), pen tools (Ink).
SIGNATURE_ANNOTATIONS = frozenset({"Ink", "Stamp", "FreeText", "FileAttachment"})
BACKGROUND_IMAGE_COVERAGE = 0.70  # a page-sized image is the scan itself, not a signature


@dataclass(frozen=True)
class Box:
    x0: float
    top: float
    x1: float
    bottom: float

    def intersects(self, other: Box) -> bool:
        return self.x0 < other.x1 and other.x0 < self.x1 and self.top < other.bottom and other.top < self.bottom

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass(frozen=True)
class VectorPath:
    box: Box
    segments: int  # curve and line segments drawn after the first move
    length: float  # approximate stroke length in points


@dataclass
class PageObjects:
    width: float = 0.0  # page size in points, to compare these boxes with OCR pixel coordinates as fractions
    height: float = 0.0
    signed_fields: list[Box] = field(default_factory=list)  # /FT /Sig widgets that carry a signature value
    annotations: list[tuple[str, Box]] = field(default_factory=list)  # signature-like annotation subtypes only
    images: list[Box] = field(default_factory=list)  # excluding page-sized backgrounds
    vectors: list[VectorPath] = field(default_factory=list)


def _name(value: Any) -> str:
    value = getattr(resolve1(value), "name", value)
    return value.decode("latin-1") if isinstance(value, bytes) else str(value)


def _inherited(data: dict[str, Any], key: str) -> Any:
    """Field attributes such as /FT may sit on the parent field rather than on the widget."""
    seen = 0
    while data is not None and seen < 10:
        if key in data:
            return data[key]
        data = resolve1(data.get("Parent"))
        seen += 1
    return None


def _bezier_length(p0: tuple[float, float], c1: tuple[float, float], c2: tuple[float, float], p3: tuple[float, float]) -> float:
    steps, total, previous = 8, 0.0, p0
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        point = (
            u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p3[1],
        )
        total += math.dist(previous, point)
        previous = point
    return total


def _vector(curve: dict[str, Any]) -> VectorPath:
    segments, length = 0, 0.0
    current: tuple[float, float] | None = None
    for command in curve.get("path") or []:
        op, points = command[0], command[1:]
        if op == "m":
            current = points[0]
        elif op == "l" and current is not None:
            length += math.dist(current, points[0])
            segments, current = segments + 1, points[0]
        elif op == "c" and current is not None and len(points) == 3:
            length += _bezier_length(current, points[0], points[1], points[2])
            segments, current = segments + 1, points[2]
    return VectorPath(Box(curve["x0"], curve["top"], curve["x1"], curve["bottom"]), segments, length)


def read_page_objects(path: Path, page_number: int) -> PageObjects:
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_number - 1]
        found = PageObjects(width=float(page.width), height=float(page.height))
        area = float(page.width * page.height) or 1.0
        for annot in page.annots:
            data = annot.get("data") or {}
            box = Box(annot["x0"], annot["top"], annot["x1"], annot["bottom"])
            subtype = _name(data.get("Subtype"))
            if subtype == "Widget":
                if _name(_inherited(data, "FT")) == "Sig" and _inherited(data, "V") is not None:
                    found.signed_fields.append(box)
            elif subtype in SIGNATURE_ANNOTATIONS:
                found.annotations.append((subtype, box))
        for image in page.images:
            box = Box(image["x0"], image["top"], image["x1"], image["bottom"])
            if box.width * box.height / area <= BACKGROUND_IMAGE_COVERAGE:
                found.images.append(box)
        found.vectors.extend(_vector(curve) for curve in page.curves)
        page.close()
        return found
