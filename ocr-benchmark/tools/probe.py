"""Look at what one engine returns for one page: timing, word count, mean confidence and the first lines.

    python tools/probe.py <engine> "<pdf name in the dataset>" [page] [--raw]

Synthetic contracts only (the generator's dataset); it prints page text.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.engines import ENGINES, make_engine  # noqa: E402
from bench.paths import PDF_DIR  # noqa: E402
from contract_extractor.common.layout import PageText  # noqa: E402
from contract_extractor.document.ocr.preprocess import preprocess  # noqa: E402
from contract_extractor.document.pdf.render import PageRenderer  # noqa: E402


def main() -> None:
    engine_key, name = sys.argv[1], sys.argv[2]
    page = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 1
    raw = "--raw" in sys.argv
    started = time.perf_counter()
    engine = make_engine(ENGINES[engine_key])
    print(f"load {time.perf_counter() - started:.2f}s")
    data = (PDF_DIR / name).read_bytes()
    with PageRenderer(data, {}) as renderer:
        gray = renderer.render_gray(page, 300)
    image = gray if raw else preprocess(gray)[0]
    for attempt in range(2):
        started = time.perf_counter()
        words = engine.read(image)
        print(f"read {attempt + 1}: {time.perf_counter() - started:.2f}s, {len(words)} words, "
              f"mean conf {sum(w.conf for w in words) / max(len(words), 1):.3f}")
    text = PageText(number=page, width=image.shape[1], height=image.shape[0], words=tuple(words), source="probe")
    for line in text.content_lines[:40]:
        print("  ", line.text)


if __name__ == "__main__":
    main()
