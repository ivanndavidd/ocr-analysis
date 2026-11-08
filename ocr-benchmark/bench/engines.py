"""The OCR engines under test, each behind the extractor's own OCRProvider interface (`read(image) -> list[Word]`).

The extractor sends every OCR call (page selection, page turns, the full read, the table-border retry) to the first
provider of its OcrRunner, so swapping the provider swaps the engine everywhere and leaves the rest of the pipeline
(preprocessing, patterns, NIK checks, signature analysis) exactly as in production.

Engines that return text lines rather than words get word boxes by splitting the line box in proportion to the
characters, since the pipeline works on words (lines are regrouped from them).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bench.paths import MODELS, TESSDATA_BEST, TESSDATA_FAST, TESSERACT_CMD
from contract_extractor.common.models import Word
from contract_extractor.document.ocr.base import OcrEngineError


@dataclass(frozen=True)
class EngineSpec:
    key: str
    label: str  # for the report
    family: str  # tesseract | rapidocr | doctr | easyocr
    options: dict[str, Any] = field(default_factory=dict)
    device: str = "cpu"
    note: str = ""


ENGINES: dict[str, EngineSpec] = {
    spec.key: spec
    for spec in [
        EngineSpec("tesseract_fast", "Tesseract 5.4 (fast models, production today)", "tesseract",
                   {"tessdata": str(TESSDATA_FAST), "lang": "ind+eng"}),
        EngineSpec("tesseract_best", "Tesseract 5.4 (best models)", "tesseract",
                   {"tessdata": str(TESSDATA_BEST), "lang": "ind+eng"}),
        EngineSpec("rapid_v6_small", "RapidOCR, PP-OCRv6 small", "rapidocr",
                   {"version": "PP-OCRv6", "det": ("id", "small"), "rec": ("id", "small")}),
        EngineSpec("rapid_v6_medium", "RapidOCR, PP-OCRv6 medium", "rapidocr",
                   {"version": "PP-OCRv6", "det": ("id", "medium"), "rec": ("id", "medium")}),
        EngineSpec("rapid_v6_tiny", "RapidOCR, PP-OCRv6 tiny", "rapidocr",
                   {"version": "PP-OCRv6", "det": ("id", "tiny"), "rec": ("id", "tiny")}),
        EngineSpec("rapid_v5_latin", "RapidOCR, PP-OCRv5 (server detection, Latin recognition)", "rapidocr",
                   {"version": "PP-OCRv5", "det": ("ch", "server"), "rec": ("latin", "mobile")}),
        EngineSpec("rapid_v4_en", "RapidOCR, PP-OCRv4 (English recognition)", "rapidocr",
                   {"version": "PP-OCRv4", "det": ("ch", "mobile"), "rec": ("en", "mobile")}),
        EngineSpec("doctr_crnn", "docTR 1.1 (DBNet ResNet-50 + CRNN VGG16)", "doctr",
                   {"det": "db_resnet50", "reco": "crnn_vgg16_bn"}),
        EngineSpec("doctr_parseq", "docTR 1.1 (DBNet ResNet-50 + PARSeq)", "doctr",
                   {"det": "db_resnet50", "reco": "parseq"}),
        EngineSpec("easyocr", "EasyOCR 1.7 (Indonesian + English)", "easyocr", {"langs": ["id", "en"]}),
    ]
}


def split_line(text: str, box: Any, conf: float) -> list[Word]:
    """Words of one recognised line, each given the share of the line box its characters take."""
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
        words.append(Word(text=token, bbox=(x0 + start * width, y0, x0 + pos * width, y1), conf=float(conf)))
    return words


def tighten(image: np.ndarray, words: list[Word]) -> list[Word]:
    """Shrink each word box to the ink inside it, as Tesseract's boxes are. Detection models pad their boxes (RapidOCR
    grows each by its unclip ratio), and the signature analysis measures ink in printed-word heights and places its
    search area from the name's box: padded boxes made every word look taller, so signatures looked smaller."""
    gray = image if image.ndim == 2 else image.mean(axis=2)
    h, w = gray.shape
    out = []
    for word in words:
        x0, y0, x1, y1 = (int(round(v)) for v in word.bbox)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            out.append(word)
            continue
        ink = gray[y0:y1, x0:x1] < 128
        rows, cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
        if rows.size == 0:
            out.append(word)
            continue
        box = (x0 + cols[0], y0 + rows[0], x0 + cols[-1] + 1, y0 + rows[-1] + 1)
        out.append(Word(text=word.text, bbox=tuple(float(v) for v in box), conf=word.conf))
    return out


def _rgb(image: np.ndarray) -> np.ndarray:
    return np.stack([image] * 3, axis=-1) if image.ndim == 2 else image


class TesseractEngine:
    is_local = True

    def __init__(self, spec: EngineSpec, threads: int) -> None:
        from contract_extractor.document.ocr.tesseract import TesseractOCR

        os.environ["TESSDATA_PREFIX"] = spec.options["tessdata"]
        os.environ["OMP_THREAD_LIMIT"] = str(threads)
        self.name = spec.key
        self._ocr = TesseractOCR(TESSERACT_CMD, lang=spec.options["lang"])
        self._ocr.check()

    def read(self, image: np.ndarray) -> list[Word]:
        return self._ocr.read(image)


class RapidEngine:
    is_local = True

    def __init__(self, spec: EngineSpec, threads: int) -> None:
        from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR

        o = spec.options
        version = OCRVersion(o["version"])
        root = MODELS / "rapidocr"
        root.mkdir(parents=True, exist_ok=True)
        self.name = spec.key
        self._ocr = RapidOCR(params={
            "Global.log_level": "error",
            "Global.model_root_dir": str(root),
            "Global.max_side_len": 2000,  # RapidOCR's default; 4000 (the whole 300 dpi page) cost 3x time and memory
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": version,
            "Det.lang_type": o["det"][0],
            "Det.model_type": ModelType(o["det"][1]),
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.ocr_version": version,
            "Rec.lang_type": o["rec"][0],
            "Rec.model_type": ModelType(o["rec"][1]),
            "Cls.engine_type": EngineType.ONNXRUNTIME,
        })

    def read(self, image: np.ndarray) -> list[Word]:
        try:
            out = self._ocr(_rgb(image), use_cls=False)
        except Exception as exc:  # noqa: BLE001 - one failed call, as the pipeline expects
            raise OcrEngineError(type(exc).__name__) from exc
        if out.txts is None:
            return []
        words: list[Word] = []
        for box, text, score in zip(out.boxes, out.txts, out.scores):
            words.extend(split_line(text, box, score))
        return tighten(image, words)


class DoctrEngine:
    is_local = True

    def __init__(self, spec: EngineSpec, threads: int) -> None:
        os.environ["DOCTR_CACHE_DIR"] = str(MODELS / "doctr")
        import torch
        from doctr.models import ocr_predictor

        torch.set_num_threads(threads)
        self.name = spec.key
        self._torch = torch
        self._model = ocr_predictor(det_arch=spec.options["det"], reco_arch=spec.options["reco"], pretrained=True,
                                    assume_straight_pages=True, preserve_aspect_ratio=True, symmetric_pad=True)

    def read(self, image: np.ndarray) -> list[Word]:
        h, w = image.shape[:2]
        try:
            with self._torch.inference_mode():
                page = self._model([_rgb(image)]).pages[0]
        except Exception as exc:  # noqa: BLE001
            raise OcrEngineError(type(exc).__name__) from exc
        words = []
        for block in page.blocks:
            for line in block.lines:
                for word in line.words:
                    (ax, ay), (bx, by) = word.geometry
                    if word.value.strip():
                        words.append(Word(text=word.value.strip(), bbox=(ax * w, ay * h, bx * w, by * h),
                                          conf=float(word.confidence)))
        return tighten(image, words)


class EasyEngine:
    is_local = True

    def __init__(self, spec: EngineSpec, threads: int) -> None:
        import torch

        torch.set_num_threads(threads)
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stubs"))  # python-bidi stand-in, see stubs/
        import easyocr

        store = MODELS / "easyocr"
        store.mkdir(parents=True, exist_ok=True)
        self.name = spec.key
        self._reader = easyocr.Reader(spec.options["langs"], gpu=spec.device == "cuda",
                                      model_storage_directory=str(store), user_network_directory=str(store),
                                      verbose=False)

    def read(self, image: np.ndarray) -> list[Word]:
        try:
            found = self._reader.readtext(image, detail=1, paragraph=False)
        except Exception as exc:  # noqa: BLE001
            raise OcrEngineError(type(exc).__name__) from exc
        words: list[Word] = []
        for box, text, conf in found:
            words.extend(split_line(text, box, conf))
        return tighten(image, words)


FAMILIES = {"tesseract": TesseractEngine, "rapidocr": RapidEngine, "doctr": DoctrEngine, "easyocr": EasyEngine}


def make_engine(spec: EngineSpec, threads: int = 2):
    return FAMILIES[spec.family](spec, threads)


def model_files(spec: EngineSpec) -> list[Path]:
    """The model files an engine loads, for their size on disk."""
    if spec.family == "tesseract":
        return [Path(spec.options["tessdata"]) / f"{lang}.traineddata" for lang in spec.options["lang"].split("+")]
    folder = MODELS / spec.family
    return [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []
