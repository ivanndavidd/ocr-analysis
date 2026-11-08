"""Worker stdio untuk backend website: satu proses Python melayani banyak file, PDF tidak pernah ditulis ke disk.

Backend menjalankan `contract-extractor serve-stdio` sebagai proses anak dan berbicara lewat stdin/stdout.
Setiap pesan adalah satu baris JSON UTF-8 yang diakhiri newline:

    worker -> backend, sekali saat siap:  {"ready": true, "claude": <bool>}   atau  {"ready": false, "error": "<kode>"}
                                           "claude": extractor Claude ikut membaca (false: pola teks saja)
    backend -> worker, per file:           {"id": 7, "filename": "<nama>.pdf", "size": 182344, "vendor": "<folder vendor>",
                                            "mode": "ocr"}
                                           lalu tepat `size` byte PDF; "vendor" boleh tidak ada atau null; "mode" "ocr"
                                           (default) atau "full_page" (tanpa OCR: halaman pertama dan terakhir utuh
                                           ke Claude, lihat full_page.py)
    worker -> backend, per file:           {"id": 7, "ok": true, "result": <FileResult>, "filename": <nama file terurai>}
                                           atau {"id": 7, "ok": false, "error": "<kode>"}

Error hanya berupa kode tetap, tidak pernah pesan exception atau isi dokumen. Log ditulis ke stderr. Worker selesai
saat backend menutup stdin; header yang rusak juga menghentikan worker, karena posisi aliran byte tidak bisa
dipercaya lagi, dan backend memulai worker baru.
"""

from __future__ import annotations

import json
import logging
from typing import Any, BinaryIO

from contract_extractor.common.config import Settings
from contract_extractor.extractor_method.llm.base import Extractor
from contract_extractor.document.ocr.engine import OcrRunner
from contract_extractor.full_page import MODE_FULL_PAGE, MODE_OCR, process_full_pages
from contract_extractor.pipeline import process_bytes
from contract_extractor.extractor_method.pattern.filename import ParsedFilename, parse_filename

log = logging.getLogger(__name__)

MAX_HEADER_BYTES = 4096
MAX_FILENAME_CHARS = 255
_CHUNK = 1024 * 1024

ERROR_BAD_HEADER = "bad_header"
ERROR_TOO_LARGE = "too_large"


def write_message(out: BinaryIO, message: dict[str, Any]) -> None:
    out.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
    out.flush()


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    """Exactly `size` bytes, or None when the backend closed the pipe first."""
    chunks, left = [], size
    while left:
        chunk = stream.read(min(left, _CHUNK))
        if not chunk:
            return None
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def _discard(stream: BinaryIO, size: int) -> bool:
    """Skip a body we will not process, so the next header is read from the right place."""
    left = size
    while left:
        chunk = stream.read(min(left, _CHUNK))
        if not chunk:
            return False
        left -= len(chunk)
    return True


def _header(line: bytes) -> tuple[int, str, int, str | None, str] | None:
    if len(line) > MAX_HEADER_BYTES or not line.endswith(b"\n"):
        return None
    try:
        header = json.loads(line)
    except ValueError:
        return None
    if not isinstance(header, dict):
        return None
    request_id, filename, size = header.get("id"), header.get("filename"), header.get("size")
    vendor, mode = header.get("vendor"), header.get("mode", MODE_OCR)
    if not isinstance(request_id, int) or isinstance(request_id, bool):
        return None
    if not isinstance(filename, str) or not 0 < len(filename) <= MAX_FILENAME_CHARS:
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return None
    if vendor is not None and (not isinstance(vendor, str) or len(vendor) > MAX_FILENAME_CHARS):
        return None
    if mode not in (MODE_OCR, MODE_FULL_PAGE):
        return None
    return request_id, filename, size, vendor, mode


def parsed_filename_json(parsed: ParsedFilename) -> dict[str, Any]:
    return {
        "name": parsed.name,
        "start": parsed.start.isoformat() if parsed.start else None,
        "end": parsed.end.isoformat() if parsed.end else None,
        "duration_months": parsed.duration_months,
        "entity": parsed.entity,
        "flags": list(parsed.flags),
    }


def serve(
    stdin: BinaryIO, stdout: BinaryIO, settings: Settings, ocr: OcrRunner | None, extractor: Extractor | None
) -> int:
    """Answer requests until stdin closes or a header cannot be trusted; returns the number of files processed."""
    max_bytes = settings.input.max_file_mb * 1024 * 1024
    write_message(stdout, {"ready": True, "claude": extractor is not None})
    served = 0
    while True:
        line = stdin.readline(MAX_HEADER_BYTES + 1)
        if not line:
            return served
        header = _header(line)
        if header is None:
            log.warning("worker stopped: unreadable request header")
            write_message(stdout, {"id": None, "ok": False, "error": ERROR_BAD_HEADER})
            return served
        request_id, filename, size, vendor, mode = header
        if size > max_bytes:
            if not _discard(stdin, size):
                return served
            write_message(stdout, {"id": request_id, "ok": False, "error": ERROR_TOO_LARGE})
            continue
        data = _read_exact(stdin, size)
        if data is None:
            return served
        if mode == MODE_FULL_PAGE:
            result = process_full_pages(data, filename, settings, extractor, vendor)  # never raises either
        else:
            result = process_bytes(data, filename, settings, ocr, extractor, vendor)  # never raises: failures become flags
        del data
        write_message(
            stdout,
            {
                "id": request_id,
                "ok": True,
                "result": result.model_dump(mode="json"),
                "filename": parsed_filename_json(parse_filename(filename)),
            },
        )
        served += 1
