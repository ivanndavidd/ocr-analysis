"""RapidOCR sebagai pembaca kedua lokal: model PP-OCRv6 small dari Baidu (PaddleOCR) lewat onnxruntime.

Tesseract tetap pembaca utama. Pembaca kedua hanya membaca ulang halaman identitas hasil scan atau foto saat NIK atau
posisi yang dibaca Tesseract masih ragu; nilai yang sama dari dua mesin membuatnya hijau (lihat `_second_read` di
pipeline). Pada benchmark 800 kontrak sintetis (2026-11-07), dari 317 sel teks yang Tesseract biarkan kuning, RapidOCR
membaca nilai yang sama pada 269 dan semuanya benar; dibaca sendiri, RapidOCR tidak lebih akurat dari Tesseract.

Model dibaca dari folder lokal dan dicek SHA-256-nya; tidak pernah diunduh saat berjalan, dan tidak ada gambar yang
keluar dari mesin.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import numpy as np

from contract_extractor.common.models import Word
from contract_extractor.document.ocr.base import OcrEngineError

# RapidOCR 3.10.0's published files (rapidocr/default_models.yaml, multi_PP-OCRv6_*_small).
MODELS = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
}
# apps/extractor/models/rapidocr in a source checkout; an installed package sets the folder in the config or the env.
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[4] / "models" / "rapidocr"
# RapidOCR's own default. The whole 300 dpi page (about 3500 px) cost 3x the time and memory on the benchmark.
MAX_SIDE_LEN = 2000
INK = 128  # gray level below which a pixel is ink, for shrinking word boxes


class RapidOcrUnavailable(Exception):
    """The second reader cannot start; the message never contains document content."""


def resolve_model_dir(configured: Path | None) -> Path:
    """The folder holding both model files: the configured one, else RAPIDOCR_MODEL_DIR, else the checkout's."""
    for candidate in (configured, os.environ.get("RAPIDOCR_MODEL_DIR"), DEFAULT_MODEL_DIR):
        if candidate and all((Path(candidate) / name).is_file() for name in MODELS):
            return Path(candidate)
    names = ", ".join(MODELS)
    raise RapidOcrUnavailable(f"RapidOCR models not found ({names}): set ocr.second_reader.model_dir or RAPIDOCR_MODEL_DIR")


def verify_models(folder: Path) -> None:
    """A changed or truncated model file would read differently from the one that was measured."""
    for name, expected in MODELS.items():
        digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if digest != expected:
            raise RapidOcrUnavailable(f"RapidOCR model {name} does not match its published SHA-256")


class RapidOCRReader:
    name = "rapidocr"
    is_local = True

    def __init__(self, model_dir: Path, threads: int = 2) -> None:
        verify_models(model_dir)
        try:  # imported here: only a worker with the second reader on loads onnxruntime
            from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR
        except ImportError as exc:
            raise RapidOcrUnavailable("rapidocr is not installed") from exc
        engine = {"engine_type": EngineType.ONNXRUNTIME, "ocr_version": OCRVersion("PP-OCRv6"), "lang_type": "id",
                  "model_type": ModelType("small")}
        params: dict[str, Any] = {
            "Global.log_level": "error",
            "Global.use_cls": False,
            "Global.max_side_len": MAX_SIDE_LEN,
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.model_path": str(model_dir / "PP-OCRv6_det_small.onnx"),  # a given path is never downloaded
            "Rec.model_path": str(model_dir / "PP-OCRv6_rec_small.onnx"),
        }
        for stage in ("Det", "Rec"):
            params.update({f"{stage}.{key}": value for key, value in engine.items()})
        try:
            self._ocr = RapidOCR(params=params)
        except Exception as exc:  # noqa: BLE001 - any failure to start means the second reader is unavailable
            raise RapidOcrUnavailable(f"RapidOCR cannot start: {type(exc).__name__}") from exc

    def read(self, image: np.ndarray) -> list[Word]:
        rgb = np.stack([image] * 3, axis=-1) if image.ndim == 2 else image
        try:
            out = self._ocr(rgb, use_cls=False)
        except Exception as exc:  # noqa: BLE001 - one failed call, as the OCR ladder expects
            raise OcrEngineError(type(exc).__name__) from exc
        if out.txts is None:
            return []
        words: list[Word] = []
        for box, text, score in zip(out.boxes, out.txts, out.scores):
            words.extend(split_line(text, box, float(score)))
        return tighten(image, words)


def split_line(text: str, box: Any, conf: float) -> list[Word]:
    """RapidOCR reads whole lines; each word gets the share of the line box its characters take."""
    points = np.asarray(box, dtype=float).reshape(-1, 2)
    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0)
    text = text.strip()
    if not text:
        return []
    words, pos, width = [], 0, (x1 - x0) / len(text)
    for token in text.split():
        start = text.index(token, pos)
        pos = start + len(token)
        words.append(Word(text=token, bbox=(x0 + start * width, y0, x0 + pos * width, y1), conf=conf))
    return words


def tighten(image: np.ndarray, words: list[Word]) -> list[Word]:
    """Each word box shrunk to the ink inside it, as Tesseract's are: RapidOCR pads its boxes, and the line grouping
    and label lookups were tuned on tight boxes. On the benchmark, padded boxes cost RapidOCR 11 of 60 contracts."""
    gray = image if image.ndim == 2 else image.mean(axis=2)
    height, width = gray.shape
    out = []
    for word in words:
        x0, y0, x1, y1 = (int(round(v)) for v in word.bbox)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, width), min(y1, height)
        ink = gray[y0:y1, x0:x1] < INK if x1 - x0 >= 2 and y1 - y0 >= 2 else None
        rows = np.flatnonzero(ink.any(axis=1)) if ink is not None else np.empty(0)
        if ink is None or rows.size == 0:
            out.append(word)
            continue
        cols = np.flatnonzero(ink.any(axis=0))
        box = (float(x0 + cols[0]), float(y0 + rows[0]), float(x0 + cols[-1] + 1), float(y0 + rows[-1] + 1))
        out.append(Word(text=word.text, bbox=box, conf=word.conf))
    return out
