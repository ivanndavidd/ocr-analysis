"""What each engine adds to the extractor's installation: its Python packages beyond the ones the extractor already
needs (requirements.lock), with their size on disk, plus its model files. Writes results/install_size.json.

    .venv/Scripts/python tools/install_size.py
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.paths import MODELS, RESULTS, ROOT, TESSDATA_BEST, TESSDATA_FAST, TESSERACT_CMD  # noqa: E402

LOCK = ROOT / "ocr-analysis" / "apps" / "extractor" / "requirements.lock"
ROOTS = {"rapidocr": ["rapidocr", "onnxruntime"], "doctr": ["python-doctr", "torch", "torchvision"],
         "easyocr": ["easyocr", "torch", "torchvision", "scipy", "scikit-image", "shapely", "pyclipper"]}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(names: list[str]) -> set[str]:
    seen: set[str] = set()
    todo = [norm(n) for n in names]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen.add(name)
        for req in dist.requires or []:
            if "extra ==" in req:
                continue
            todo.append(norm(re.split(r"[ ;<>=!~\[(]", req, maxsplit=1)[0]))
    return seen


def size_mb(name: str) -> float:
    dist = metadata.distribution(name)
    total = 0
    for f in dist.files or []:
        p = Path(dist.locate_file(f))
        if p.is_file():
            total += p.stat().st_size
    return total / 2**20


def folder_mb(folder: Path, pattern: str = "*") -> float:
    return sum(p.stat().st_size for p in folder.rglob(pattern) if p.is_file()) / 2**20


def main() -> None:
    base = {norm(line.split("==")[0]) for line in LOCK.read_text(encoding="utf-8").splitlines() if "==" in line}
    out = {}
    for family, roots in ROOTS.items():
        extra = sorted(closure(roots) - base)
        out[family] = {"packages": {n: round(size_mb(n), 1) for n in extra}}
        out[family]["packages_mb"] = round(sum(out[family]["packages"].values()), 1)
    tesseract_dir = TESSERACT_CMD.parent
    out["tesseract"] = {
        "packages": {"Tesseract 5.4 program (without language data)": round(
            folder_mb(tesseract_dir) - folder_mb(tesseract_dir / "tessdata"), 1)},
        "note": "pytesseract is already an extractor dependency; the program is installed separately (apt in the image)",
    }
    out["tesseract"]["packages_mb"] = out["tesseract"]["packages"]["Tesseract 5.4 program (without language data)"]
    out["models_mb"] = {
        "tesseract_fast (ind+eng)": round(sum((TESSDATA_FAST / f"{l}.traineddata").stat().st_size for l in ("ind", "eng")) / 2**20, 1),
        "tesseract_best (ind+eng)": round(sum((TESSDATA_BEST / f"{l}.traineddata").stat().st_size for l in ("ind", "eng")) / 2**20, 1),
        **{f"rapidocr {p.stem}": round(p.stat().st_size / 2**20, 1) for p in sorted((MODELS / "rapidocr").glob("*.onnx"))},
        **{f"doctr {p.stem}": round(p.stat().st_size / 2**20, 1) for p in sorted((MODELS / "doctr").rglob("*.pt"))},
        **{f"easyocr {p.stem}": round(p.stat().st_size / 2**20, 1) for p in sorted((MODELS / "easyocr").glob("*.pth"))},
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "install_size.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for family in ("tesseract", *ROOTS):
        print(f"{family:10} +{out[family]['packages_mb']:8.1f} MB  {', '.join(out[family]['packages'])[:150]}")
    for k, v in out["models_mb"].items():
        print(f"  model {k:40} {v:7.1f} MB")


if __name__ == "__main__":
    main()
