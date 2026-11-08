"""Pattern mode (no Claude) on every file of a website job, with today's fixes on ("after") or patched off ("before").

Usage: local_ab.py <job.json> <before|after> <out.json> [workers]

"before" switches off, in this process only:
  fix 1  a missing signature page again stops the identity page from being read (P_id emptied, so nothing is read)
  fix 2  debris-only OCR lines stay in the page text
  fix 3  no reread with table borders erased for a missing NIK or position
  fix 4  a NIK picked from a longer digit run is not flagged
Fix 5 (Claude's unsigned cutoff) has no effect without Claude.
"""
import json
import os
import sys
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

FOLDER = Path("C:/Users/ASUS/OneDrive/kontrak-testing")


def _patch_before():
    import contract_extractor.pipeline as pipeline
    from contract_extractor.common.layout import PageText

    select = pipeline.select_pages

    def old_select(*args):
        sel = select(*args)
        return replace(sel, p_id=[], ttd_guess=None) if sel.p_ttd is None else sel

    pipeline.select_pages = old_select
    PageText.content_lines = property(lambda self: self.lines)
    pipeline._reveal_table_rows = lambda *args, **kwargs: None
    find = pipeline.find_nik

    def old_find(pages):
        read = find(pages)
        read.log.deret_panjang = False
        return read

    pipeline.find_nik = old_find


_state = {}


def _init(mode):
    if mode == "before":
        _patch_before()
    from contract_extractor.common.config import load_settings
    from contract_extractor.document.ocr.engine import build_runner

    _state["settings"] = settings = load_settings(None)
    _state["runner"] = build_runner(settings)


def _one(item):
    import contract_extractor.pipeline as pipeline

    name, path, vendor = item
    r = pipeline.process_file(FOLDER / path, _state["settings"], _state["runner"], None, vendor)
    return name, {
        "name": r.nama_karyawan,
        "nik": r.nik,
        "position": r.posisi,
        "start": r.tanggal_mulai.isoformat() if r.tanggal_mulai else None,
        "end": r.tanggal_selesai.isoformat() if r.tanggal_selesai else None,
        "signature": r.status_ttd.value if r.status_ttd else None,
        "decision": r.keputusan.value if r.keputusan else None,
        "reasons": list(r.log.alasan),
        "p_id": r.log.p_id,
        "p_ttd": r.log.p_ttd,
        "nik_status": r.log.nik.status.value if r.log.nik else None,
        "components": {str(k): v for k, v in r.log.komponen_halaman.items()},
    }


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    mode, out = sys.argv[2], Path(sys.argv[3])
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    truth = {t["file_name"]: t for t in job["truth"]}
    items = [(f["name"], f["path"], truth[f["name"]]["meta"]["company"]) for f in job["job"]["files"]]
    only = os.environ.get("ONLY")  # comma-separated name prefixes, for a quick check
    if only:
        items = [i for i in items if i[0].startswith(tuple(only.split(",")))]
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    results = {}
    with Pool(workers, initializer=_init, initargs=(mode,)) as pool:
        for i, (name, row) in enumerate(pool.imap_unordered(_one, items), 1):
            results[name] = row
            if i % 10 == 0:
                print(f"{mode}: {i}/{len(items)}", flush=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{mode}: done, {len(results)} files -> {out}")


if __name__ == "__main__":
    main()
