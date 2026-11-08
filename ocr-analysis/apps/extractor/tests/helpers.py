"""Builders for synthetic PDFs and pages. All names and numbers here are fictional."""

from __future__ import annotations

import os
import shutil
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

from contract_extractor.extractor_method.llm.base import DateAnswer, ExtractionResult, TextAnswer
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import Word

PAGE_W, PAGE_H = 612.0, 792.0
_CHAR_W, _GAP, _WORD_H = 5.5, 3.0, 11.0


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class PageExtras:
    """Things drawn on top of a page body. Boxes are (x, y_top, width, height) in points, y from the top."""

    ops: str = ""  # raw content-stream operators in PDF coordinates (y from the bottom)
    annots: list[tuple[str, str]] = field(default_factory=list)  # (annotation dict entries, appearance ops)
    images: list[tuple[np.ndarray, tuple[float, float, float, float]]] = field(default_factory=list)
    signed_field: tuple[float, float, float, float] | None = None  # a /FT /Sig widget with a signature value


def pdf_rect(box: tuple[float, float, float, float]) -> str:
    x, y_top, w, h = box
    return f"{x} {PAGE_H - y_top - h} {x + w} {PAGE_H - y_top}"


def _image_object(body: np.ndarray | None) -> bytes:
    if isinstance(body, np.ndarray):
        h, w = body.shape
        data = zlib.compress(body.astype(np.uint8).tobytes())
        header = f"/Width {w} /Height {h} /Filter /FlateDecode /Length {len(data)}"
    else:
        data, header = b"\xff", "/Width 1 /Height 1 /Length 1"
    return (
        f"<< /Type /XObject /Subtype /Image /ColorSpace /DeviceGray /BitsPerComponent 8 {header} >>".encode()
        + b"\nstream\n"
        + data
        + b"\nendstream"
    )


def _stream(data: bytes, entries: str = "") -> bytes:
    return f"<< {entries} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def make_pdf(path: Path, pages: list[tuple[str, object]], extras: dict[int, PageExtras] | None = None) -> Path:
    """Write a minimal valid PDF.

    Page kinds: ("text", "line\\nline") flowing from the top-left, ("layout", [(x, y_top, text), ...]),
    ("image", "") for a blank full-page image, ("image", ndarray) for a full-page grayscale scan, ("blank", "").
    `extras` maps a 0-based page index to drawings, annotations, small images, or a signed signature field.
    """
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids: list[int] = []
    sig_fields: list[int] = []
    for index, (kind, body) in enumerate(pages):
        xobjects: list[str] = []
        if kind == "text":
            ops = ["BT /F1 11 Tf 14 TL 50 750 Td"]
            ops += [f"({_escape(line)}) Tj T*" for line in str(body).split("\n")]
            ops.append("ET")
            stream = "\n".join(ops).encode("latin-1")
        elif kind == "layout":
            ops = [f"BT /F1 11 Tf {x} {PAGE_H - y - 9} Td ({_escape(t)}) Tj ET" for x, y, t in body]  # type: ignore[union-attr]
            stream = "\n".join(ops).encode("latin-1")
        elif kind == "image":
            objects.append(_image_object(body if isinstance(body, np.ndarray) else None))
            xobjects.append(f"/Im1 {len(objects)} 0 R")
            stream = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
        else:
            stream = b""

        extra = (extras or {}).get(index, PageExtras())
        annot_refs: list[int] = []
        if extra.ops:
            stream += b"\n" + extra.ops.encode("latin-1")
        for n, (image, (x, y_top, w, h)) in enumerate(extra.images, start=2):
            objects.append(_image_object(image))
            xobjects.append(f"/Im{n} {len(objects)} 0 R")
            stream += f"\nq {w} 0 0 {h} {x} {PAGE_H - y_top - h} cm /Im{n} Do Q".encode()
        for entries, appearance in extra.annots:
            ap = ""
            if appearance:
                rect = entries.split("/Rect [", 1)[1].split("]", 1)[0]
                objects.append(_stream(appearance.encode("latin-1"), f"/Type /XObject /Subtype /Form /BBox [{rect}]"))
                ap = f" /AP << /N {len(objects)} 0 R >>"
            objects.append(f"<< /Type /Annot {entries}{ap} /F 4 >>".encode())
            annot_refs.append(len(objects))
        if extra.signed_field:
            objects.append(
                f"<< /Type /Annot /Subtype /Widget /FT /Sig /T (sig{index}) /Rect [{pdf_rect(extra.signed_field)}]"
                " /V << /Type /Sig /Filter /Adobe.PPKLite >> /F 4 >>".encode()
            )
            annot_refs.append(len(objects))
            sig_fields.append(len(objects))

        resources = "/Font << /F1 3 0 R >>" + (f" /XObject << {' '.join(xobjects)} >>" if xobjects else "")
        annots = f" /Annots [{' '.join(f'{r} 0 R' for r in annot_refs)}]" if annot_refs else ""
        objects.append(_stream(stream))
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << {resources} >>"
            f" /Contents {content_id} 0 R{annots} >>".encode()
        )
        kids.append(len(objects))

    acroform = f" /AcroForm << /Fields [{' '.join(f'{r} 0 R' for r in sig_fields)}] /SigFlags 3 >>" if sig_fields else ""
    objects[0] = f"<< /Type /Catalog /Pages 2 0 R{acroform} >>".encode()
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def page_text(number: int, rows: list[tuple[float, float, str]]) -> PageText:
    """Build a PageText from (y_top, x, text) rows with approximate word boxes."""
    words: list[Word] = []
    for y, x, text in rows:
        cursor = x
        for token in text.split():
            width = _CHAR_W * len(token)
            words.append(Word(text=token, bbox=(cursor, y, cursor + width, y + _WORD_H), conf=1.0))
            cursor += width + _GAP
    return PageText(number=number, width=PAGE_W, height=PAGE_H, words=tuple(words), source="text_layer")


def flow(lines: list[str], start_y: float = 60.0, x: float = 50.0, leading: float = 14.0) -> list[tuple[float, float, str]]:
    return [(start_y + i * leading, x, line) for i, line in enumerate(lines)]


# Lines stay under ~95 characters so rendered scans do not clip words at the right page edge.
BODY_FILLER = "Pihak Pertama dan Pihak Kedua sepakat bahwa ketentuan ini wajib dipatuhi selama kontrak."

# A valid NIK layout for a man born 1 January 1990, with regency and district 99, which no real NIK uses.
EMPLOYEE_NIK = "3199990101900001"
IDENTITY_LINES = [
    "PERJANJIAN KERJA WAKTU TERTENTU",
    "Nomor: 001/PKWT/I/2026",
    "Pada hari ini telah dibuat dan ditandatangani perjanjian kerja oleh dan antara para pihak berikut:",
    "Nama : Andi Wijaya",
    "Jabatan : Direktur",
    "selanjutnya disebut PIHAK PERTAMA, bertindak untuk dan atas nama PT Contoh Sejahtera.",
    "Nama : Budi Santoso",
    f"NIK : {EMPLOYEE_NIK}",
    "Tempat/Tanggal Lahir : Bandung, 1 Januari 1990",
    "Jenis Kelamin : Laki-laki",
    "selanjutnya disebut PIHAK KEDUA atau Tenaga Kerja yang bertindak untuk diri sendiri.",
    "Para Pihak sepakat mengikatkan diri dalam perjanjian kerja dengan ketentuan sebagai berikut:",
]
PERIOD_LINES = [
    "Pasal 1 Jangka Waktu",
    "Perjanjian ini berlaku efektif mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026.",
]
SALARY_LINES = [
    "Pasal 3 Upah",
    "Gaji pokok Pihak Kedua sebesar Rp 7.500.000,- dibayarkan setiap akhir bulan oleh Pihak Pertama.",
    "Tunjangan transport diberikan sesuai kebijakan perusahaan yang berlaku bagi seluruh karyawan.",
]
CLOSING_LINE = "Demikian perjanjian ini dibuat rangkap dua dan ditandatangani para pihak dalam keadaan sadar."


def signature_rows(block_top: float, employee: str = "Budi Santoso") -> list[tuple[float, float, str]]:
    """Two-column signature block: company on the left, employee on the right."""
    return [
        (block_top - 28, 50.0, CLOSING_LINE),
        (block_top, 50.0, "Jakarta, 1 Januari 2026"),
        (block_top + 18, 80.0, "PIHAK PERTAMA"),
        (block_top + 18, 380.0, "PIHAK KEDUA"),
        (block_top + 98, 80.0, "Andi Wijaya"),
        (block_top + 98, 380.0, employee),
        (block_top + 112, 80.0, "Direktur"),
        (block_top + 112, 380.0, "Tenaga Kerja"),
    ]


def body_page(number: int, extra: list[str] | None = None, until_y: float = 720.0) -> PageText:
    lines = list(extra or [])
    while 60 + len(lines) * 14 < until_y:
        lines.append(BODY_FILLER)
    return page_text(number, flow(lines))


# Signature spots above the printed names of signature_rows(350): employee on the right, company on the left.
EMPLOYEE_SIGNATURE_BOX = (385.0, 408.0, 70.0, 38.0)
COMPANY_SIGNATURE_BOX = (85.0, 408.0, 70.0, 38.0)
CORNER_PARAF_BOX = (40.0, 740.0, 30.0, 20.0)


def scribble_ops(box: tuple[float, float, float, float], line_width: float = 1.5) -> str:
    """A pen-like zigzag of Bezier curves filling the box, in PDF coordinates."""
    x, y_top, w, h = box
    base = PAGE_H - y_top - h
    ops = [f"{line_width} w 1 J 1 j", f"{x:.1f} {base + 0.4 * h:.1f} m"]
    n = 8
    for i in range(n):
        x0, x1 = x + w * i / n, x + w * (i + 1) / n
        high, low = (base + h, base) if i % 2 == 0 else (base, base + h)
        end_y = base + h * (0.3 + 0.4 * ((i * 37) % 10) / 10)
        ops.append(f"{x0 + 0.3 * (x1 - x0):.1f} {high:.1f} {x0 + 0.7 * (x1 - x0):.1f} {low:.1f} {x1:.1f} {end_y:.1f} c")
    ops.append("S")
    return "\n".join(ops)


def scribble_image(width: int = 420, height: int = 200) -> np.ndarray:
    """A grayscale image of a handwritten-looking signature on white, for image-type e-signatures."""
    image = np.full((height, width), 255, np.uint8)
    xs = np.linspace(10, width - 10, 200)
    ys = height / 2 + (height / 3) * np.sin(xs / 18) * np.cos(xs / 47)
    cv2.polylines(image, [np.stack([xs, ys], axis=1).astype(np.int32)], False, 0, thickness=7, lineType=cv2.LINE_AA)
    return image


def signature_extras(*marks: str) -> PageExtras:
    """Signing marks for the closing page: vector, annotation, image, digital, company_vector, corner_paraf."""
    extras = PageExtras()
    for mark in marks:
        if mark == "vector":
            extras.ops += "\n" + scribble_ops(EMPLOYEE_SIGNATURE_BOX)
        elif mark == "company_vector":
            extras.ops += "\n" + scribble_ops(COMPANY_SIGNATURE_BOX)
        elif mark == "annotation":
            extras.annots.append((f"/Subtype /Ink /Rect [{pdf_rect(EMPLOYEE_SIGNATURE_BOX)}]", scribble_ops(EMPLOYEE_SIGNATURE_BOX)))
        elif mark == "corner_paraf":
            extras.annots.append((f"/Subtype /Ink /Rect [{pdf_rect(CORNER_PARAF_BOX)}]", scribble_ops(CORNER_PARAF_BOX, 1.0)))
        elif mark == "image":
            extras.images.append((scribble_image(), EMPLOYEE_SIGNATURE_BOX))
        elif mark == "digital":
            extras.signed_field = EMPLOYEE_SIGNATURE_BOX
        else:
            raise ValueError(mark)
    return extras


def signed_contract_pdf(
    path: Path, *marks: str, employee: str = "Budi Santoso", appendix: tuple[tuple[str, object], PageExtras] | None = None
) -> Path:
    """Five-page native contract: identity on page 1, salary on page 2, mid-page signature block on page 5.

    Without marks nobody has signed; `marks` (see signature_extras) adds signatures to the closing page.
    `appendix` adds a sixth page (make_pdf page spec and its extras) after the closing page.
    """
    closing_page = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6) + signature_rows(350.0, employee)
    pages: list[tuple[str, object]] = [
        ("text", "\n".join(IDENTITY_LINES + PERIOD_LINES)),
        ("text", "\n".join(SALARY_LINES + [BODY_FILLER] * 40)),
        ("text", "\n".join([BODY_FILLER] * 45)),
        ("text", "\n".join([BODY_FILLER] * 45)),
        ("layout", [(x, y, text) for y, x, text in closing_page]),
    ]
    extras = {4: signature_extras(*marks)}
    if appendix:
        pages.append(appendix[0])
        extras[len(pages) - 1] = appendix[1]
    return make_pdf(path, pages, extras)


def scan_pdf_from(native: Path, out: Path, dpi: int = 200, skew_deg: float = 0.0) -> Path:
    """Rasterize every page of a native PDF into an image-only PDF, optionally rotated like a crooked scan."""
    pdf = pdfium.PdfDocument(str(native))
    pages: list[tuple[str, object]] = []
    try:
        for page in pdf:
            image = np.array(page.render(scale=dpi / 72, grayscale=True).to_numpy(), dtype=np.uint8)
            image = image[:, :, 0] if image.ndim == 3 else image
            if skew_deg:
                h, w = image.shape
                matrix = cv2.getRotationMatrix2D((w / 2, h / 2), skew_deg, 1.0)
                image = cv2.warpAffine(image, matrix, (w, h), borderValue=255)
            pages.append(("image", image))
            page.close()
    finally:
        pdf.close()
    return make_pdf(out, pages)


WORD_FOOTER = "PT Contoh Sejahtera Abadi - Jl. Contoh No. 1, Jakarta 10110"  # 59 characters, in the footer band
WORD_MARGIN = 72.0  # Word's default one-inch margin


def pasted_photo_pdf_from(native: Path, out: Path, dpi: int = 200, footer: str = WORD_FOOTER) -> Path:
    """Photograph every page, paste each photo inside Word's default margins, add a footer, export to PDF."""
    pdf = pdfium.PdfDocument(str(native))
    pages: list[tuple[str, object]] = []
    extras: dict[int, PageExtras] = {}
    width = PAGE_W - 2 * WORD_MARGIN
    box = (WORD_MARGIN, WORD_MARGIN, width, width * PAGE_H / PAGE_W)
    try:
        for index, page in enumerate(pdf):
            image = np.array(page.render(scale=dpi / 72, grayscale=True).to_numpy(), dtype=np.uint8)
            extras[index] = PageExtras(images=[(image[:, :, 0] if image.ndim == 3 else image, box)])
            pages.append(("layout", [(WORD_MARGIN, 760.0, footer)]))
            page.close()
    finally:
        pdf.close()
    return make_pdf(out, pages, extras)


def find_tesseract() -> Path | None:
    """Locate a local Tesseract for tests: TESSERACT_CMD, PATH, then the per-user Windows install."""
    candidates = [
        os.environ.get("TESSERACT_CMD"),
        shutil.which("tesseract"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


PERIOD_QUOTE = "mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026"


def llm_answer(
    name: str | None = "Budi Santoso",
    start: date | None = date(2026, 1, 1),
    end: date | None = date(2026, 12, 31),
    *,
    name_quote: str = "Nama : Budi Santoso",
    period_quote: str = PERIOD_QUOTE,
    confidence: float = 0.95,
    position: str | None = None,
    position_quote: str | None = None,
    vendor: str | None = None,
    vendor_quote: str | None = None,
) -> ExtractionResult:
    """An extractor answer in the output schema, every field quoted from the fictional test contract."""
    return ExtractionResult(
        nama_karyawan=TextAnswer(kutipan=name_quote if name else None, nilai=name, confidence=confidence),
        posisi=TextAnswer(kutipan=position_quote, nilai=position, confidence=confidence if position else 0.0),
        tanggal_mulai=DateAnswer(kutipan=period_quote if start else None, nilai=start, confidence=confidence),
        tanggal_selesai=DateAnswer(kutipan=period_quote if end else None, nilai=end, confidence=confidence),
        vendor=TextAnswer(kutipan=vendor_quote, nilai=vendor, confidence=confidence if vendor else 0.0),
    )
