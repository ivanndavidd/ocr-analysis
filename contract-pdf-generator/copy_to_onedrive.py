"""Copy contract_generator.py output into a OneDrive folder laid out like the contract SharePoint.

    <dest>/<vendor>/<period>/<contract>.pdf
    e.g. kontrak-testing/PT Bina Semesta/Januari 2026 - Maret 2026/Budi Santoso - 1jan-31mar26 - SNA.pdf

Run it with the contract-ocr venv (from the contract-ocr folder):
    .venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py --dry-run
    .venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py
    .venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py --replace
    .venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py --src D:/synthetic --dest "D:/OneDrive/kontrak-testing"

Only the PDFs are copied: the ground truth stays in the generator output, the way the real SharePoint holds only
contracts. A file with the same name is replaced. Without --replace nothing else in the destination is touched; with
it, the PDFs in <dest>/<vendor>/<period>/ that this output does not place (an earlier run's) go to the Windows
Recycle Bin after the copy, so they can still be restored, and folders left empty are removed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import filecmp
import json
import os
import re
import shutil
import stat
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
         "September", "Oktober", "November", "Desember"]
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_folder(name):
    """A folder name Windows and OneDrive accept: no reserved characters, no trailing dot or space."""
    return _UNSAFE.sub("", name).strip().rstrip(". ")


def period_folder(rec):
    """`meta.period_folder` from the generator; older outputs without it get the same name from the dates."""
    folder = rec["meta"].get("period_folder")
    if folder:
        return folder
    start, end = (dt.date.fromisoformat(rec[k]) for k in ("contract_start", "contract_end"))
    return f"{BULAN[start.month - 1]} {start.year} - {BULAN[end.month - 1]} {end.year}"


def default_dest():
    onedrive = os.environ.get("OneDriveCommercial") or os.environ.get("OneDrive")
    return Path(onedrive) / "kontrak-testing" if onedrive else None


def to_recycle_bin(paths):
    """Move files to the Windows Recycle Bin (SHFileOperationW with undo), not a hard delete."""
    import ctypes
    from ctypes import wintypes

    class FileOp(ctypes.Structure):  # SHFILEOPSTRUCTW
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

    names = "".join(f"{p}\0" for p in paths) + "\0"  # each path NUL-ended, then one more NUL closes the list
    buffer = (ctypes.c_wchar * len(names))(*names)
    flags = 0x40 | 0x10 | 0x4 | 0x400  # FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI
    op = FileOp(wFunc=3, pFrom=ctypes.addressof(buffer), fFlags=flags)  # 3 = FO_DELETE
    failed = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if failed or op.fAnyOperationsAborted:
        sys.exit(f"could not move the old PDFs to the Recycle Bin (error {failed:#x}); the new ones are copied")


def remove_empty_folders(dest):
    """The <vendor>/<period>/ folders an earlier run left empty, then the vendor folders left empty; returns how many
    could not be removed. OneDrive marks its folders read-only, which blocks rmdir, so that flag is cleared first."""
    stuck = 0
    for pattern in ("*/*", "*"):
        for folder in dest.glob(pattern):
            if folder.is_dir() and not any(folder.iterdir()):
                try:
                    os.chmod(folder, stat.S_IWRITE)
                    folder.rmdir()
                except OSError:  # still held by OneDrive's sync; the next --replace run tries again
                    stuck += 1
    return stuck


def main():
    parser = argparse.ArgumentParser(description="Copy generated contract PDFs into <dest>/<vendor>/<period>/.")
    parser.add_argument("--src", type=Path, default=SCRIPT_DIR / "output",
                        help="generator output folder, holding ground_truth.json and pdf/ (default: output/ here)")
    parser.add_argument("--dest", type=Path, default=default_dest(),
                        help=f"destination root (default: {default_dest() or 'none found, pass --dest'})")
    parser.add_argument("--dry-run", action="store_true", help="list what would be copied without copying")
    parser.add_argument("--replace", action="store_true",
                        help="afterwards move <dest>/<vendor>/<period>/ PDFs this output does not place to the "
                             "Recycle Bin, so the destination holds this run only")
    args = parser.parse_args()
    if args.dest is None:
        parser.error("no OneDrive folder found on this PC; pass --dest")
    if args.replace and os.name != "nt":
        parser.error("--replace uses the Windows Recycle Bin")

    ground_truth = args.src / "ground_truth.json"
    if not ground_truth.is_file():
        sys.exit(f"{ground_truth} not found: run contract_generator.py first, or point --src at its output folder")
    records = json.loads(ground_truth.read_text(encoding="utf-8"))
    dest = args.dest.resolve()

    states, folders, placed = Counter(), Counter(), set()
    for rec in records:
        pdf = args.src / "pdf" / rec["file_name"]
        if not pdf.is_file():
            states["missing from --src"] += 1
            continue
        folder = dest / safe_folder(rec["meta"]["company"]) / safe_folder(period_folder(rec))
        target = folder / rec["file_name"]
        placed.add(os.path.normcase(target))
        if not target.exists():
            state = "new"
        else:
            state = "unchanged" if filecmp.cmp(pdf, target, shallow=False) else "replaced"
        states[state] += 1
        folders[folder.relative_to(dest).as_posix()] += 1
        if args.dry_run or state == "unchanged":
            continue
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, target)

    print(f"{'Dry run, nothing copied. Would place' if args.dry_run else 'Placed'} {sum(folders.values())} PDFs "
          f"in {len(folders)} folders under {dest}")
    for name, count in sorted(folders.items()):
        print(f"  {name}: {count}")
    print("files:", dict(states))

    if args.replace:
        old = [p for p in dest.glob("*/*/*.pdf") if os.path.normcase(p) not in placed]
        if old and not args.dry_run:
            to_recycle_bin(old)
        print(f"{'Would move' if args.dry_run else 'Moved'} {len(old)} PDFs of an earlier run to the Recycle Bin")
        stuck = 0 if args.dry_run else remove_empty_folders(dest)
        if stuck:
            print(f"{stuck} empty folders could not be removed yet (OneDrive still syncing?); run --replace again later")


if __name__ == "__main__":
    main()
