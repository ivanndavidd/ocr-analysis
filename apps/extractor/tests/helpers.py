"""Builders for synthetic PDFs and pages. All names and numbers here are fictional."""

from __future__ import annotations

import os
import shutil
import zlib
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

from contract_extractor.layout import PageText
from contract_extractor.models import Word

PAGE_W, PAGE_H = 612.0, 792.0
_CHAR_W, _GAP, _WORD_H = 5.5, 3.0, 11.0


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, pages: list[tuple[str, object]]) -> Path:
    """Write a minimal valid PDF.

    Page kinds: ("text", "line\\nline") flowing from the top-left, ("layout", [(x, y_top, text), ...]),
    ("image", "") for a blank full-page image, ("image", ndarray) for a full-page grayscale scan, ("blank", "").
    """
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids: list[int] = []
    for kind, body in pages:
        resources = "/Font << /F1 3 0 R >>"
        if kind == "text":
            ops = ["BT /F1 11 Tf 14 TL 50 750 Td"]
            ops += [f"({_escape(line)}) Tj T*" for line in str(body).split("\n")]
            ops.append("ET")
            stream = "\n".join(ops).encode("latin-1")
        elif kind == "layout":
            ops = [f"BT /F1 11 Tf {x} {PAGE_H - y - 9} Td ({_escape(t)}) Tj ET" for x, y, t in body]  # type: ignore[union-attr]
            stream = "\n".join(ops).encode("latin-1")
        elif kind == "image":
            if isinstance(body, np.ndarray):
                h, w = body.shape
                data = zlib.compress(body.astype(np.uint8).tobytes())
                header = f"/Width {w} /Height {h} /Filter /FlateDecode /Length {len(data)}"
            else:
                data, header = b"\xff", "/Width 1 /Height 1 /Length 1"
            objects.append(
                f"<< /Type /XObject /Subtype /Image /ColorSpace /DeviceGray /BitsPerComponent 8 {header} >>"
                .encode()
                + b"\nstream\n"
                + data
                + b"\nendstream"
            )
            resources += f" /XObject << /Im1 {len(objects)} 0 R >>"
            stream = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
        else:
            stream = b""
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << {resources} >>"
            f" /Contents {content_id} 0 R >>".encode()
        )
        kids.append(len(objects))

    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
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

IDENTITY_LINES = [
    "PERJANJIAN KERJA WAKTU TERTENTU",
    "Nomor: 001/PKWT/I/2026",
    "Pada hari ini telah dibuat dan ditandatangani perjanjian kerja oleh dan antara para pihak berikut:",
    "Nama : Andi Wijaya",
    "Jabatan : Direktur",
    "selanjutnya disebut PIHAK PERTAMA, bertindak untuk dan atas nama PT Contoh Sejahtera.",
    "Nama : Budi Santoso",
    "NIK : 3171012345670001",
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


def signed_contract_pdf(path: Path) -> Path:
    """Five-page native contract: identity on page 1, salary on page 2, mid-page signature block on page 5."""
    closing_page = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6) + signature_rows(350.0)
    return make_pdf(
        path,
        [
            ("text", "\n".join(IDENTITY_LINES + PERIOD_LINES)),
            ("text", "\n".join(SALARY_LINES + [BODY_FILLER] * 40)),
            ("text", "\n".join([BODY_FILLER] * 45)),
            ("text", "\n".join([BODY_FILLER] * 45)),
            ("layout", [(x, y, text) for y, x, text in closing_page]),
        ],
    )


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
