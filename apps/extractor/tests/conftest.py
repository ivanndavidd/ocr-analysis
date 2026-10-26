"""Synthetic fixtures only. Never copy real contracts from sample/ into tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, pages: list[tuple[str, str]]) -> Path:
    """Write a minimal valid PDF. Page kinds: ("text", body) | ("image", "") | ("blank", "")."""
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids: list[int] = []
    for kind, body in pages:
        resources = "/Font << /F1 3 0 R >>"
        if kind == "text":
            ops = ["BT /F1 11 Tf 14 TL 50 750 Td"]
            ops += [f"({_escape(line)}) Tj T*" for line in body.split("\n")]
            ops.append("ET")
            stream = "\n".join(ops).encode("latin-1")
        elif kind == "image":
            objects.append(
                b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray"
                b" /BitsPerComponent 8 /Length 1 >>\nstream\n\xff\nendstream"
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
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


IDENTITY_PAGE = "\n".join(
    [
        "PERJANJIAN KERJA WAKTU TERTENTU",
        "Nama : Budi Santoso",
        "NIK : 3171012345670001",
        "Jangka waktu berlaku efektif mulai tanggal 1 Januari 2026",
        "sampai dengan tanggal 31 Desember 2026.",
    ]
)


@pytest.fixture
def contracts_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "in"
    folder.mkdir()
    make_pdf(
        folder / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf",
        [
            ("text", IDENTITY_PAGE),
            ("text", "Pasal 2\nPara pihak sepakat bahwa ketentuan dalam pasal ini berlaku selama masa kontrak."),
            ("blank", ""),
        ],
    )
    make_pdf(folder / "Rina Wulandari - 12 bulan.pdf", [("image", ""), ("image", "")])
    (folder / "Dewi Lestari - 1jan-31mar26.pdf").write_bytes(b"%PDF-1.4\nthis is not a real pdf body")
    (folder / "catatan.txt").write_text("bukan pdf", encoding="utf-8")
    return folder
